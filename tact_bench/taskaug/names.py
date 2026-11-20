"""Curated identity pools for minting twin users.

Every name here is deliberately DISJOINT from the base retail dataset — none of
these first or last names is used by any existing user, nor by any third party
named in a task instruction (e.g. the pickup proxies "Hannah Park" / "Priya
Patel" or the cousin "Daniel Ruiz"). Keeping the pools disjoint guarantees a twin
never collides with an existing ``find_user_id_by_name_zip`` /
``find_user_by_username`` target and that rule-based name replacement in the
instruction can never accidentally rewrite an unrelated person's name.

First names are split by gender and each twin's first name is drawn from the SAME
gender pool as the base user's: instructions refer to renamed people with
gendered pronouns ("my spouse David ... he does the pharmacy runs"), so a
cross-gender rename would contradict the surrounding text. ``BASE_NAME_GENDER``
records the gender of every base-dataset first name for that lookup.

The pools are large enough that a whole 23-user base suite can be twinned many
times over (8+ copies) without exhausting them.
"""

# 21 female first names, none present in the base dataset or its instructions.
FEMALE_FIRST_NAMES = [
    "Nadia", "Ingrid", "Camille", "Rosa", "Freya", "Aria", "Celeste", "Esme",
    "Greta", "Iris", "Kira", "Nora", "Paloma", "Rhea", "Tessa", "Wren", "Yara",
    "Aisha", "Dahlia", "Helena", "Juno",
]

# 19 male first names, none present in the base dataset or its instructions.
MALE_FIRST_NAMES = [
    "Marcus", "Gabriel", "Yusuf", "Diego", "Theo", "Malik", "Bruno", "Dmitri",
    "Felix", "Hugo", "Jonas", "Mateo", "Otto", "Silas", "Viktor", "Xander",
    "Zane", "Cyrus", "Quinn",
]

# Gender of every first name in the base dataset, so a twin keeps the base
# user's gender and the instruction's pronouns stay coherent. "Alex" is male:
# the receipt-resend scenario refers to the account owner as "he".
BASE_NAME_GENDER = {
    "Mina": "F", "Sarah": "F", "Lisa": "F", "Elena": "F", "Lena": "F",
    "Rachel": "F", "Ava": "F", "Jasmine": "F", "Natalie": "F", "Lauren": "F",
    "Megan": "F", "Sophie": "F", "Chloe": "F", "Emily": "F", "Olivia": "F",
    "Jason": "M", "Omar": "M", "Farid": "M", "Nathan": "M", "Ethan": "M",
    "David": "M", "Noah": "M", "Alex": "M",
}

# 40 last names, none present in the base dataset or its instructions
# (existing surnames AND third-party surnames like Park / Patel / Ruiz excluded).
LAST_NAMES = [
    "Delgado", "Novak", "Fitzgerald", "Okafor", "Sinclair", "Vance", "Whitfield",
    "Bauer", "Castellano", "Draper", "Espinoza", "Fairbanks", "Goldberg",
    "Halloran", "Ishikawa", "Jovanovic", "Lindqvist", "Montgomery", "Nakamura",
    "Osei", "Pemberton", "Ridgeway", "Solberg", "Thornton", "Underwood",
    "Vasquez", "Wexler", "Yamamoto", "Zielinski", "Ashford", "Bellamy",
    "Cavanaugh", "Donahue", "Eriksson", "Fontaine", "Grimaldi", "Hawthorne",
    "Larsson", "Marchetti", "Okonkwo",
]

# Street names for a fresh address1; the city/state area is preserved by the
# caller (only the house number + street changes), so no city coupling is needed.
STREET_NAMES = [
    "Aspen Court", "Brookhaven Lane", "Cypress Terrace", "Dunmore Avenue",
    "Elmwood Circle", "Fallbrook Road", "Glenview Street", "Harborline Way",
    "Ivywood Drive", "Juniper Hollow", "Kingsley Boulevard", "Larkspur Lane",
    "Maplecrest Avenue", "Northgate Road", "Overlook Terrace", "Pinecrest Court",
    "Quarry Ridge Road", "Rosemont Street", "Sagebrook Lane", "Thornfield Way",
    "Underwood Avenue", "Vantage Point Drive", "Westbury Court", "Yarrow Lane",
]
