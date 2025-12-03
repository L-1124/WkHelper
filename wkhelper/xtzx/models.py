from typing import TypedDict


class Course(TypedDict):
    name: str
    classroom_id: int
    sign: str
    product_id: int
    sku_id: int


class UserInfo(TypedDict):
    name: str
    school: str
