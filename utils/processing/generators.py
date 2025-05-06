import random
import string


def generate_password(length: int = 12) -> str:
    characters = {
        "lower": string.ascii_lowercase,
        "upper": string.ascii_uppercase,
        "digits": string.digits,
        "symbols": "!@#&*"
    }

    password = [
        random.choice(characters["lower"]),
        random.choice(characters["upper"]),
        random.choice(characters["digits"]),
        random.choice(characters["symbols"]),
    ]

    all_chars = "".join(characters.values())
    password += random.choices(all_chars, k=length - 4)

    random.shuffle(password)
    return ''.join(password)


def generate_username(length: int = 12) -> str:
    username = ""

    special_chars = ['_', 'x', 'z']
    for _ in range(length):
        choice = random.random()

        if choice < 0.7:
            username += random.choice(string.ascii_lowercase)
        elif choice < 0.9:
            username += random.choice(string.digits)
        else:
            username += random.choice(special_chars)

    if random.random() < 0.5:
        username += str(random.randint(0, 9999))

    return username
