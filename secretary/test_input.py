import sys

print("Type something in Korean and press Enter:")
user_input = sys.stdin.readline().strip()
print(f"You typed: {user_input}")
print(f"Characters: {[c for c in user_input]}")
