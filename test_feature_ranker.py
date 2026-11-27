from rapidfuzz.distance import Levenshtein

# Hai chữ Nôm
nom_a = "學"
nom_b = "觉"

nom_distance = Levenshtein.distance(nom_a, nom_b)

# Hai chữ Quốc ngữ
viet_a = "hòa"
viet_b = "hoà"

viet_distance = Levenshtein.distance(viet_a, viet_b)

print(nom_distance)
print(viet_distance)