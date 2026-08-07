from copy import deepcopy
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings

app = FastAPI(title="POS Local Mock API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

class Item(BaseModel):
    id: str
    title: str
    meta: str = ""
    done: bool = False

SEED: dict[str, Any] = {
    "dashboard": {"name": "Rajesh", "date": "Saturday, July 25", "next_best_action": "Finish breakfast, then head to the office.", "routine_progress": 0.33, "calories": 1040, "protein": 76, "water_liters": 1.2, "weight_lb": 171.2, "spent_today": 51},
    "timeline": [{"id": f"r{i}", "title": title, "meta": time, "done": i < 4} for i, (title, time) in enumerate([("Warm water","7:00 AM"),("Brush","7:10 AM"),("Shower","7:25 AM"),("Breakfast","8:00 AM"),("Office","9:00 AM"),("Lunch","12:30 PM"),("Gym","6:00 PM"),("Dinner","8:00 PM"),("Sleep","10:45 PM")], 1)],
    "routines": {"morning": ["Warm water", "Brush", "Shower", "Breakfast"], "afternoon": ["Lunch"], "evening": ["Gym", "Dinner", "Sleep"]},
    "nutrition": {"calories": 1040, "calorie_target": 2200, "protein": 76, "protein_target": 150, "water": 1.2, "water_target": 3.0, "meals": ["Greek yogurt, berries & oats", "Chicken rice bowl"]},
    "weight": {"current_lb": 171.2, "weekly_change_lb": -0.8, "goal_lb": 165.0, "history": [172.0,171.8,171.9,171.5,171.6,171.3,171.2]},
    "workouts": {"title": "Chest and Triceps", "exercises": ["Bench Press","Incline Dumbbell Press","Cable Fly","Triceps Pushdown","Walking"]},
    "money": {"spent_today": 51, "monthly_budget": 1500, "remaining": 1102, "notice": "Based on manually logged transactions.", "transactions": [{"title":"Lunch","amount":-14},{"title":"Coffee","amount":-5},{"title":"Groceries","amount":-32}]},
    "tasks": [{"id": f"t{i}", "title": title, "meta": "Today", "done": False} for i, title in enumerate(["Finish React feature","Grocery shopping","Gym","Read 20 minutes","Plan tomorrow"], 1)],
    "focus": {"active": False, "remaining_seconds": 1500, "today_minutes": 52},
    "reports": {"routine_completion": .82, "average_calories": 2080, "average_protein": 132, "average_water": 2.6, "workouts_completed": 4, "spending": 348, "tasks_completed": 23, "focus_minutes": 258},
    "notifications": [{"id":"n1","title":"Lunch is coming up","meta":"Your plan says 12:30 PM","done":False}],
    "profile": {"name":"Rajesh","joined":"January 2026","priorities":["Health","Focus","Finances"]},
    "settings": {"units":"imperial","currency":"USD","week_start":"Monday","theme":"system","notifications":True},
}
store = deepcopy(SEED)

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/{resource}")
def get_resource(resource: str):
    if resource == "search": return []
    if resource not in store: raise HTTPException(404, "Unknown resource")
    return store[resource]

@app.post("/{resource}")
def add_item(resource: str, item: Item):
    if resource not in store or not isinstance(store[resource], list): raise HTTPException(400, "Resource does not accept items")
    store[resource].insert(0, item.model_dump())
    return item

@app.patch("/{resource}/{item_id}")
def update_item(resource: str, item_id: str, patch: dict[str, Any]):
    items = store.get(resource)
    if not isinstance(items, list): raise HTTPException(400, "Resource is not a list")
    for item in items:
        if item.get("id") == item_id:
            item.update(patch)
            return item
    raise HTTPException(404, "Item not found")

@app.delete("/{resource}/{item_id}", status_code=204)
def delete_item(resource: str, item_id: str):
    items = store.get(resource)
    if not isinstance(items, list): raise HTTPException(400, "Resource is not a list")
    store[resource] = [item for item in items if item.get("id") != item_id]

@app.post("/demo/reset")
def reset_demo():
    global store
    store = deepcopy(SEED)
    return {"status": "reset"}
