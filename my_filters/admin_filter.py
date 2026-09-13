from aiogram.filters import BaseFilter
from aiogram.types import Message
from database import Database # Импортируйте ваш класс Database

class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        # Вызываем метод из  класса Database
        return await Database.is_admin(message.from_user.id)
