import time
from ruqqus.classes import *
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import *

from ruqqus.__main__ import app, db

@app.route("/api/flag/post/<pid>", methods=["POST"])
@is_not_banned
def api_flag_post(pid, v):

    post=get_post(pid)

    kind = request.form.get("report_type")
    
    if kind=="admin":
        existing=db.query(Flag).filter_by(user_id=v.id, post_id=pid).first()

        if existing:
            return "",409

        flag=Flag(post_id=post.id,
                  user_id=v.id,
                  created_utc=int(time.time())
                  )
        
    elif kind=="guild":
        existing=db.query(Report).filter_by(user_id=v.id, post_id=pid).first()

        if existing:
            return "",409

        flag=Report(post_id=pid,
                  user_id=v.id,
                  created_utc=int(time.time())
                  )
    else:
        return "",422
        

    db.add(flag)

    db.commit()
    return "", 204


@app.route("/api/flag/comment/<cid>", methods=["POST"])
@is_not_banned
def api_flag_comment(cid, v):

    cid=base36decode(cid)

    existing=db.query(CommentFlag).filter_by(user_id=v.id, comment_id=cid).first()

    if existing:
        return "",409

    flag=CommentFlag(comment_id=cid,
              user_id=v.id,
              created_utc=int(time.time())
              )

    db.add(flag)

    db.commit()
    return "", 204

