import json
from pathlib import Path

b1_cache = Path('data/KimVânKiềuTânTruyện_金雲翹新傳/cache/phan_loai.json')
data = json.loads(b1_cache.read_text(encoding='utf-8'))

# The book has 302 pages.
# Structure:
# 1-14: intro
# 15-286: alternating T2 (pairs of Nom and pairs of QN)
#   Nom: 4k+15, 4k+16 -> ch
#   QN:  4k+17, 4k+18 -> vi
# 287-302: misc / appendix

nom_pages = set()
qn_pages = set()
for k in range(68): # 68 * 2 = 136 pages each
    nom_pages.add(4*k + 15)
    nom_pages.add(4*k + 16)
    qn_pages.add(4*k + 17)
    qn_pages.add(4*k + 18)

print(f"Total Nom pages: {len(nom_pages)}, min={min(nom_pages)}, max={max(nom_pages)}")
print(f"Total QN pages: {len(qn_pages)}, min={min(qn_pages)}, max={max(qn_pages)}")

for pnum_str, pinfo in data['pages'].items():
    pnum = int(pnum_str)
    if pnum in nom_pages:
        pinfo['tag'] = 'ch'
    elif pnum in qn_pages:
        pinfo['tag'] = 'vi'
    elif pnum <= 14:
        pinfo['tag'] = 'intro'
    else:
        pinfo['tag'] = 'misc'

b1_cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print("Successfully updated phan_loai.json tags for Book 1!")
