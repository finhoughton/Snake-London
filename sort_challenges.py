import json

path = "challenges.json"

with open(path) as f:
    data = json.load(f)

for c in data["challenges"]:
    c["difficulty"] = float(c["difficulty"])

data["challenges"].sort(key=lambda c: c["difficulty"]) # type: ignore

with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

with open("challenges_list.txt", "w") as f:
    for c in data["challenges"]:
        f.write(f"{c['name']} ({c['difficulty']}): {c['description']}\n")

print(len(data["challenges"]))

curses_path = "curses.json"

with open(curses_path) as f:
    curses_data = json.load(f)

with open(curses_path, "w") as f:
    json.dump(curses_data, f, indent=2)
    f.write("\n")

with open("curses_list.txt", "w") as f:
    for c in curses_data["curses"]:
        f.write(f"{c['name']}: {c['description']}\n")

print(len(curses_data["curses"]))
