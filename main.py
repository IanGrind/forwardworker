# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/main.py

from database import initialize_database

# Initialize the database BEFORE importing the Bot class.
# This ensures that when plugins are loaded, 'db' is a valid object.
initialize_database()

from bot import Bot

if __name__ == "__main__":
    # Create and run the bot instance.
    app = Bot()
    app.run()
