import requests
import json

url = "https://nba-api-free-data.p.rapidapi.com/nba-scoreboard-by-date"

headers = {
    'x-rapidapi-key': "5eee166535msh22626074ec6c874p14f687jsn40784b838787",
    'x-rapidapi-host': "nba-api-free-data.p.rapidapi.com"
}

params = {"date": "20250120"}

response = requests.get(url, headers=headers, params=params)

# Parse and pretty print the JSON response
json_data = response.json()
print(json.dumps(json_data, indent=2))
