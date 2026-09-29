from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SystemSnapshot(BaseModel):
    gateway: str
    bank: str
    merchant: str
    ledger: str
    webhook: str
    overall: str | None = None


class MessageResponse(BaseModel):
    ok: bool = True
    message: str
