"""Evaluation harness for FinProof.

Evals are first-class here, not an afterthought. An autonomous financial agent is
only as trustworthy as its *measured* behavior, so we score every run against a
labeled golden set and gate releases on the safety-critical metric — quarantine
recall (did we catch everything we were supposed to refuse to post?).

    python -m evals.run          # print the scorecard
    pytest tests/test_evals.py   # enforce the gates as a regression test
"""
