import unittest
from scripts.go_live.inspect_github_review_evidence import screen_reviews
SHA="a"*40
def pr(): return {"number":77,"head":{"sha":SHA},"user":{"login":"owner"}}
def review(state="APPROVED",sha=SHA,user="reviewer",kind="User"):
    return {"id":101,"state":state,"commit_id":sha,
            "user":{"login":user,"type":kind},"submitted_at":"2026-10-25T13:00:00Z"}
class ReviewSourceTests(unittest.TestCase):
    def test_no_review_blocks(self):
        self.assertEqual(screen_reviews(pr(),[],SHA)["reason"],"NO_EXACT_HEAD_APPROVED_REVIEW")
    def test_author_and_bot_do_not_count(self):
        self.assertEqual(screen_reviews(pr(),[review(user="owner"),review(user="bot",kind="Bot")],SHA)["exact_head_review_candidates"],[])
    def test_stale_sha_does_not_count(self):
        self.assertEqual(screen_reviews(pr(),[review(sha="b"*40)],SHA)["exact_head_review_candidates"],[])
    def test_apparent_approval_still_requires_independence(self):
        x=screen_reviews(pr(),[review()],SHA)
        self.assertEqual(x["result"],"BLOCKED")
        self.assertEqual(len(x["exact_head_review_candidates"]),1)
        self.assertFalse(x["reviewer_independence_verified"])
    def test_head_mismatch_blocks(self):
        self.assertEqual(screen_reviews(pr(),[review()],"b"*40)["reason"],"HEAD_IDENTITY_MISMATCH")
if __name__=="__main__":
    unittest.main()
