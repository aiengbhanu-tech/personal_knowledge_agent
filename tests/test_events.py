from app.ingestion import event_candidates


def test_explicit_event_labels_are_detected_without_action_verbs():
    events = list(event_candidates("Decision: USD 99 per month.\nAction item: send the contract.\nCommitment: follow up Friday."))
    assert [kind for kind, _ in events] == ["decision", "commitment", "commitment"]
