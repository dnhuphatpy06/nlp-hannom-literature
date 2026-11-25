import os, json

base_dir = r"D:\Academic_document\Term_5_year_3\NLP\LabMid\nlp-hannom-literature\data"
for d in os.listdir(base_dir):
    if "Kim" in d and "Truy" in d and "2" not in d and "_" in d:
        target_dir = os.path.join(base_dir, d, "vi")
        lp = os.path.join(target_dir, "Label.txt")
        print("Folder:", d)
        print("Label path:", lp)
        with open(lp, "r", encoding="utf-8") as f:
            lines = f.readlines()
        print("Total lines:", len(lines))
        for idx in range(min(5, len(lines))):
            parts = lines[idx].strip().split("\t")
            fname = parts[0]
            boxes = json.loads(parts[1])
            print(f"\n--- Line {idx}: {fname} (boxes: {len(boxes)}) ---")
            for b_idx in range(min(3, len(boxes))):
                print(f"  Box {b_idx}: pos={boxes[b_idx]['points'][0]} | text={boxes[b_idx]['transcription']}")
