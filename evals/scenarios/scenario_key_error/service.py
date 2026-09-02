def get_user_role(user):
    return user["role"]


if __name__ == "__main__":
    print(get_user_role({"name": "alex"}))
