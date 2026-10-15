from pydantic import BaseModel


class LoginRequest(BaseModel):
    student_id: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str
