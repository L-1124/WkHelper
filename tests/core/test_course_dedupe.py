"""课程引用与去重测试：同一慕课课程挂在多个课堂下时不能互相顶掉。"""

from wkhelper.core.entities import Course, course_reference, dedupe_courses


def test_dedupe_courses_keeps_distinct_classrooms() -> None:
    """主班与子班 course.id 相同但 classroom_id 不同，两条都要保留。"""
    main = Course(id=5279451, name="人工智能通识 - 主班", platform_id="ykt", metadata={"classroom_id": 31322391})
    sub = Course(id=5279451, name="人工智能通识 - 子班", platform_id="ykt", metadata={"classroom_id": 31322392})

    result = dedupe_courses([main, sub])

    assert [course.metadata["classroom_id"] for course in result] == [31322391, 31322392]
    assert [course_reference(course) for course in result] == ["5279451:31322391", "5279451:31322392"]


def test_dedupe_courses_falls_back_to_course_id() -> None:
    """没有 classroom_id 的平台按 course.id 去重，并保留先出现的一条。"""
    first = Course(id=1, name="课程1", platform_id="xtzx")
    second = Course(id=1, name="课程1（重复）", platform_id="xtzx")

    result = dedupe_courses([first, second])

    assert result == [first]
    assert course_reference(first) == "1"


def test_dedupe_courses_drops_repeated_classroom() -> None:
    """同一课堂重复出现时只保留首条。"""
    first = Course(id=1, name="课程1", platform_id="ykt", metadata={"classroom_id": 10})
    duplicated = Course(id=1, name="课程1（重复）", platform_id="ykt", metadata={"classroom_id": 10})

    assert dedupe_courses([first, duplicated]) == [first]
