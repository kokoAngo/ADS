"""检查Notion数据库字段结构"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
TRAINING_DATABASE_ID = "30b1c197-4dad-80ec-9ea3-d63db4c0ace9"

notion_headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 获取数据库结构
url = f"https://api.notion.com/v1/databases/{TRAINING_DATABASE_ID}"
response = requests.get(url, headers=notion_headers, timeout=30)
db_info = response.json()

print("数据库名称:", db_info.get("title", [{}])[0].get("plain_text", "Unknown"))
print("\n字段列表:")
for name, prop in db_info.get("properties", {}).items():
    print(f"  {name}: {prop['type']}")

# 获取一条示例数据
url = f"https://api.notion.com/v1/databases/{TRAINING_DATABASE_ID}/query"
response = requests.post(url, headers=notion_headers, json={"page_size": 1}, timeout=30)
data = response.json()

if data.get("results"):
    print("\n示例数据:")
    page = data["results"][0]
    for name, prop in page.get("properties", {}).items():
        value = None
        if prop["type"] == "number":
            value = prop.get("number")
        elif prop["type"] == "rich_text" and prop.get("rich_text"):
            value = prop["rich_text"][0]["plain_text"]
        elif prop["type"] == "title" and prop.get("title"):
            value = prop["title"][0]["plain_text"]
        elif prop["type"] == "select" and prop.get("select"):
            value = prop["select"]["name"]
        print(f"  {name}: {value}")
