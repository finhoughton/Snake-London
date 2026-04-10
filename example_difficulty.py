from map import Map
from challenges import get_difficulty, neck_weights

m = Map()

examples: list[tuple[str, str, str]] = [
    ("Victoria", "Walthamstow Central", "Finsbury Park"),
    ("Victoria", "Seven Sisters", "Euston"),
    ("N Circle", "Wood Lane", "Liverpool Street"),
    ("Picc", "Park Royal", "Green Park"),
    ("Mildmay", "West Hampstead", "Canonbury"),
    ("W&C", "Bank", "Waterloo"),
    ("Central", "Notting Hill Gate", "Bank"),
    ("Bakerloo", "Waterloo", "Oxford Circus"),
]

for line, start, end in examples:
    path = m._path_between_on_line(line, start, end)
    neck = path[1:]
    xs = neck_weights(m, line, neck)
    diff = get_difficulty(xs)
    names = [m.get_station(s).display_name for s in neck]
    print(f"{m.get_line(line).display_name}: {m.get_station(start).display_name} → {m.get_station(end).display_name}")
    print(f"  Neck ({len(neck)}): {' → '.join(names)}")
    print(f"  Weights: {xs}")
    print(f"  Difficulty: {diff:.2f}")
    print()
