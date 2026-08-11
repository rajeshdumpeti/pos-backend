"""HTTP request and response contracts for the local mock API."""

from pydantic import BaseModel


class Item(BaseModel):
    id: str
    title: str
    meta: str = ""
    done: bool = False
