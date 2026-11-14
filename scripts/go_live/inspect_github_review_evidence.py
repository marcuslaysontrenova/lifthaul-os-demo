#!/usr/bin/env python3
"""Read-only GitHub evidence screening for LiftHaul PR #77.

A real APPROVED review on the final SHA is necessary, but never sufficient
to certify independence, human identity or authorization for production.
"""
import argparse
import json
import re
import urllib.request

REPO = "marcuslaysontrenova/lifthaul-os-demo"
PR = 77

def screen_reviews(pr, reviews, expected_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        return {"result":"FAIL","reason":"INVALID_SHA"}
    if not isinstance(pr,dict) or pr.get("number")!=PR or pr.get("head",{}).get("sha")!=expected_sha:
        return {"result":"BLOCKED","reason":"HEAD_IDENTITY_MISMATCH"}
    author=(pr.get("user") or {}).get("login")
    candidates=[]
    for review in reviews if isinstance(reviews,list) else []:
        user=(review.get("user") or {}).get("login")
        if (review.get("state")=="APPROVED" and review.get("commit_id")==expected_sha
                and user and user!=author and not (review.get("user") or {}).get("type")=="Bot"):
            candidates.append({"review_id":review.get("id"),"reviewer":user,
                               "submitted_at":review.get("submitted_at"),
                               "review_commit":expected_sha})
    return {"result":"BLOCKED","reason":"INDEPENDENT_HUMAN_ELIGIBILITY_PENDING"
            if candidates else "NO_EXACT_HEAD_APPROVED_REVIEW",
            "exact_head_review_candidates":candidates,
            "reviewer_independence_verified":False}

def fetch_json(path):
    url="https://api.github.com/repos/"+REPO+path
    request=urllib.request.Request(url,headers={"Accept":"application/vnd.github+json",
                                                "User-Agent":"LiftHaul-Review-Evidence/1.0"})
    with urllib.request.urlopen(request,timeout=20) as response:
        return json.load(response)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--expected-sha",required=True)
    args=p.parse_args()
    try:
        pr=fetch_json(f"/pulls/{PR}")
        reviews=fetch_json(f"/pulls/{PR}/reviews?per_page=100")
        result=screen_reviews(pr,reviews,args.expected_sha)
    except Exception:
        result={"result":"BLOCKED","reason":"GITHUB_SOURCE_UNAVAILABLE"}
    print(json.dumps(result,indent=2))
    return 1

if __name__=="__main__":
    raise SystemExit(main())
