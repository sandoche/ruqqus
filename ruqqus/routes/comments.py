from urllib.parse import urlparse
import mistletoe
from sqlalchemy import func
from bs4 import BeautifulSoup

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.base36 import *
from ruqqus.helpers.sanitize import *
from ruqqus.helpers.filters import *
from ruqqus.helpers.embed import *
from ruqqus.helpers.markdown import *
from ruqqus.helpers.get import *
from ruqqus.helpers.session import *
from ruqqus.classes import *
from flask import *
from ruqqus.__main__ import app, db, limiter
from werkzeug.contrib.atom import AtomFeed
from datetime import datetime

@app.route("/comment/<cid>", methods=["GET"])
def comment_cid(cid):

    comment=get_comment(cid)
    return redirect(comment.permalink)

@app.route("/post/<p_id>/<anything>/<c_id>", methods=["GET"])
@app.route("/api/v1/post/<p_id>/comment/<c_id>", methods=["GET"])
@auth_desired
@api
def post_pid_comment_cid(p_id, c_id, anything=None, v=None):

    comment=get_comment(c_id, v=v)

    post=get_post(p_id, v=v)

    if comment.parent_submission != post.id:
        abort(404)

    board=post.board

    if board.is_banned and not (v and v.admin_level > 3):
        return {'html':lambda:render_template("board_banned.html",
                               v=v,
                               b=board),

                'api':lambda:{'error':f'+{board.name} is banned.'}

                }

    if post.over_18 and not (v and v.over_18) and not session_over18(comment.board):
        t=int(time.time())
        return {'html':lambda:render_template("errors/nsfw.html",
                               v=v,
                               t=t,
                               lo_formkey=make_logged_out_formkey(t),
                               board=comment.board
                               ),
                'api':lambda:{'error':f'This content is not suitable for some users and situations.'}

                }

    if post.is_nsfl and not (v and v.hide_nsfl) and not session_isnsfl(comment.board):
        t=int(time.time())
        return {'html':lambda:render_template("errors/nsfl.html",
                               v=v,
                               t=t,
                               lo_formkey=make_logged_out_formkey(t),
                               board=comment.board
                               ),

                'api':lambda:{'error':f'This content is not suitable for some users and situations.'}

                }


    #check guild ban
    board=post.board
    if board.is_banned and v.admin_level<3:
        return {'html':lambda:render_template("board_banned.html",
                               v=v,
                               b=board),
                'api':lambda:{'error':f'+{board.name} is banned.'}
                }

    post._preloaded_comments=[comment]

    #context improver
    context=min(int(request.args.get("context", 0)), 5)
    c=comment
    while context > 0 and not c.is_top_level:

        parent=c.parent
        post._preloaded_comments+=[parent]

        c=parent
        context -=1
    top_comment=c

    sort_type=request.args.get("sort", "hot")
    #children comments
    current_ids=[comment.id]
    for i in range(6-context):
        if g.v:
            votes=db.query(CommentVote).filter(CommentVote.user_id==g.v.id).subquery()

            comms=db.query(
                Comment,
                User,
                Title,
                votes.c.vote_type
                ).filter(
                Comment.parent_comment_id.in_(current_ids)
                ).join(Comment.author).join(
                User.title,
                isouter=True
                ).join(
                votes,
                votes.c.comment_id==Comment.id,
                isouter=True
                )

            if sort_type=="hot":
                comments=comms.order_by(Comment.score_hot.asc()).all()
            elif sort_type=="top":
                comments=comms.order_by(Comment.score_top.asc()).all()
            elif sort_type=="new":
                comments=comms.order_by(Comment.created_utc.desc()).all()
            elif sort_type=="disputed":
                comments=comms.order_by(Comment.score_disputed.asc()).all()
            elif sort_type=="random":
                c=comms.all()
                comments=random.sample(c, k=len(c))
            else:
                abort(422)


            output=[]
            for c in comms:
                com=c[0]
                com._title=c[2]
                com._voted=c[3] if c[3] else 0
                output.append(com)
        else:
            comms=db.query(
                Comment,
                User,
                Title
                ).filter(
                Comment.parent_comment_id.in_(current_ids)
                ).join(Comment.author).join(
                User.title,
                isouter=True
                )

            if sort_type=="hot":
                comments=comms.order_by(Comment.score_hot.asc()).all()
            elif sort_type=="top":
                comments=comms.order_by(Comment.score_top.asc()).all()
            elif sort_type=="new":
                comments=comms.order_by(Comment.created_utc.desc()).all()
            elif sort_type=="disputed":
                comments=comms.order_by(Comment.score_disputed.asc()).all()
            elif sort_type=="random":
                c=comms.all()
                comments=random.sample(c, k=len(c))
            else:
                abort(422)

            output=[]
            for c in comms:
                com=c[0]
                com._title=c[2]
                output.append(com)

        post._preloaded_comments+=output

        current_ids=[x.id for x in output]

        
    return {'html':lambda:post.rendered_page(v=g.v, comment=top_comment, comment_info=comment),
            'api':lambda:c.json
            }

@app.route("/api/comment", methods=["POST"])
@limiter.limit("6/minute")
@is_not_banned
@tos_agreed
@validate_formkey
def api_comment(v):

    parent_submission=base36decode(request.form.get("submission"))
    parent_fullname=request.form.get("parent_fullname")

    #process and sanitize
    body=request.form.get("body","")[0:10000]

    with CustomRenderer(post_id=request.form.get("submission")) as renderer:
        body_md=renderer.render(mistletoe.Document(body))
    body_html=sanitize(body_md, linkgen=True)

    #Run safety filter
    bans=filter_comment_html(body_html)

    if bans:
        return render_template("comment_failed.html",
                               action="/api/comment",
                               parent_submission=request.form.get("submission"),
                               parent_fullname=request.form.get("parent_fullname"),
                               badlinks=[x.domain for x in bans],
                               body=body,
                               v=v
                               ), 422

    #check existing
    existing=db.query(Comment).filter_by(author_id=v.id,
                                         body=body,
                                         parent_fullname=parent_fullname,
                                         parent_submission=parent_submission
                                         ).first()
    if existing:
        return redirect(existing.permalink)

    #get parent item info
    parent_id=int(parent_fullname.split("_")[1], 36)
    if parent_fullname.startswith("t2"):
        parent=db.query(Submission).filter_by(id=parent_id).first()
        parent_comment_id=None
        level=1
    elif parent_fullname.startswith("t3"):
        parent=db.query(Comment).filter_by(id=parent_id).first()
        parent_comment_id=parent.id
        level=parent.level+1

    #No commenting on deleted/removed things
    if parent.is_banned or parent.is_deleted:
        abort(403)

    #check for ban state
    post = get_post(request.form.get("submission"))
    if post.is_archived or not post.board.can_comment(v):
        abort(403)

        
    #create comment
    c=Comment(author_id=v.id,
              body=body,
              body_html=body_html,
              parent_submission=parent_submission,
              parent_fullname=parent_fullname,
              parent_comment_id=parent_comment_id,
              level=level,
              author_name=v.username,
              over_18=post.over_18,
              is_nsfl=post.is_nsfl,
              is_op=(v.id==post.author_id)
              )

    db.add(c)
    db.commit()

    c.determine_offensive()

    notify_users=set()

    #queue up notification for parent author
    if parent.author.id != v.id:
        notify_users.add(parent.author.id)

    #queue up notifications for username mentions
    soup=BeautifulSoup(c.body_html, features="html.parser")
    mentions=soup.find_all("a", href=re.compile("^/@(\w+)"), limit=3)
    for mention in mentions:
        username=mention["href"].split("@")[1]
        user=db.query(User).filter_by(username=username).first()
        if user:
            notify_users.add(user.id)


    for x in notify_users:
        n=Notification(comment_id=c.id,
                       user_id=x)
        db.add(n)
    db.commit()
                           

    #create auto upvote
    vote=CommentVote(user_id=v.id,
                     comment_id=c.id,
                     vote_type=1
                     )

    db.add(vote)
    db.commit()

    #print(f"Content Event: @{v.username} comment {c.base36id}")

    return redirect(f"{c.permalink}?context=1")


@app.route("/edit_comment/<cid>", methods=["POST"])
@is_not_banned
@validate_formkey
@api
def edit_comment(cid, v):

    c = get_comment(cid)

    if not c.author_id == v.id:
        abort(403)

    if c.is_banned or c.is_deleted:
        abort(403)

    if c.board.has_ban(v):
        abort(403)
        
    body = request.form.get("body", "")[0:10000]
    with CustomRenderer(post_id=c.post.base36id) as renderer:
        body_md=renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)

    #Run safety filter
    bans=filter_comment_html(body_html)

    if bans:
        return {'html':lambda:render_template("comment_failed.html",
                               action=f"/edit_comment/{c.base36id}",
                               badlinks=[x.domain for x in bans],
                               body=body,
                               v=v
                               ),
                'api':lambda:{'error':f'A blacklist domain was used.'}
                }

    c.body=body
    c.body_html=body_html
    c.edited_utc = int(time.time())

    db.add(c)
    db.commit()

    c.determine_offensive()

    path=request.form.get("current_page","/")

    return redirect(f"{path}#comment-{c.base36id}")

@app.route("/delete/comment/<cid>", methods=["POST"])
@app.route("/api/v1/delete/comment/<cid>", methods=["POST"])
@auth_required
@validate_formkey
@api
def delete_comment(cid, v):

    c=db.query(Comment).filter_by(id=base36decode(cid)).first()

    if not c:
        abort(404)

    if not c.author_id==v.id:
        abort(403)

    c.is_deleted=True

    db.add(c)
    db.commit()

    cache.delete_memoized(User.commentlisting, v)

    return "", 204



@app.route("/embed/comment/<cid>", methods=["GET"])
@app.route("/embed/post/<pid>/comment/<cid>", methods=["GET"])
@app.route("/api/vi/embed/comment/<cid>", methods=["GET"])
@app.route("/api/vi/embed/post/<pid>/comment/<cid>", methods=["GET"])
@api
def embed_comment_cid(cid, pid=None):

    comment=get_comment(cid)

    if not comment.parent:
        abort(403)

    if comment.is_banned or comment.is_deleted:
        return {'html':lambda:render_template("embeds/comment_removed.html", c=comment),
                'api':lambda:{'error':f'Comment {cid} has been removed'}
               }

    if comment.board.is_banned:
        abort(410)

    return render_template("embeds/comment.html", c=comment)

