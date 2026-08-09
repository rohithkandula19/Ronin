from board.task import Task, from_dict, pending


def test_task_defaults_to_not_done():
    assert Task("t1", "Write it down").done is False


def test_round_trips_through_a_dict():
    task = Task("t2", "Ship it", True)
    assert from_dict(task.to_dict()) == task


def test_reads_a_row_without_a_done_flag():
    assert from_dict({"id": "t3", "title": "Old row"}).done is False


def test_pending_keeps_input_order():
    tasks = [Task("a", "A"), Task("b", "B", True), Task("c", "C")]
    assert [task.id for task in pending(tasks)] == ["a", "c"]
