from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Role = Literal["admin", "staff", "customer"]
Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
PositiveRate = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8)]
Name = Annotated[str, Field(min_length=1, max_length=100)]


# ---------- auth / users ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=1, max_length=120)


class AdminUserIn(RegisterIn):
    role: Role = "customer"


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)


# ---------- reference data (staff-managed) ----------
class InstitutionIn(BaseModel):
    name: Name


class AccountTypeIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=40)]
    is_investment: bool = False


class AssetClassIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=40)]


class SecurityIn(BaseModel):
    symbol: Annotated[str, Field(min_length=1, max_length=12)]
    name: Name
    asset_class_id: int

    @field_validator("symbol")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.strip().upper()


class PriceIn(BaseModel):
    price_date: date
    close_price: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]


# ---------- categories / rules ----------
class CategoryIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=60)]
    kind: Literal["income", "expense", "transfer"]
    is_system: bool = False          # only staff/admin may create system-wide categories


class RuleIn(BaseModel):
    keyword: Annotated[str, Field(min_length=2, max_length=60)]
    category_id: int
    is_system: bool = False


# ---------- accounts ----------
class AccountIn(BaseModel):
    institution_id: int
    account_type_id: int
    name: Annotated[str, Field(min_length=1, max_length=80)]
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    opening_balance: Money = Decimal("0")
    currency_code: str = Field(default="PHP", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    fx_rate_to_php: PositiveRate | None = None


class AccountUpdate(BaseModel):
    institution_id: int | None = None
    account_type_id: int | None = None
    name: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    opening_balance: Money | None = None
    fx_rate_to_php: PositiveRate | None = None
    is_active: bool | None = None


class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    from_amount: PositiveMoney
    to_amount: PositiveMoney
    txn_date: date | None = None
    note: str | None = Field(default=None, max_length=100)


# ---------- transactions ----------
class TransactionIn(BaseModel):
    account_id: int
    txn_date: date
    description: Annotated[str, Field(min_length=1, max_length=255)]
    amount: Money
    category_id: int | None = None       # omitted -> rules + AI pick the category
    fx_rate_to_php: PositiveRate | None = None

    @field_validator("amount")
    @classmethod
    def nonzero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("amount cannot be zero")
        return v


class TransactionUpdate(BaseModel):
    txn_date: date | None = None
    description: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    amount: Money | None = None
    category_id: int | None = None
    fx_rate_to_php: PositiveRate | None = None


class ClassifyRow(BaseModel):
    description: Annotated[str, Field(min_length=1, max_length=255)]
    amount: Money
    fx_rate_to_php: PositiveRate | None = None


class ClassifyIn(BaseModel):
    rows: list[ClassifyRow] = Field(min_length=1, max_length=200)


class ImportRow(BaseModel):
    txn_date: date
    description: Annotated[str, Field(min_length=1, max_length=255)]
    amount: Money
    fx_rate_to_php: PositiveRate | None = None
    category_id: int | None = None
    category_source: Literal["manual", "rule", "ai", "default"] | None = None
    ai_confidence: float | None = Field(default=None, ge=0, le=1)


class ImportIn(BaseModel):
    account_id: int
    rows: list[ImportRow] = Field(min_length=1, max_length=200)


# ---------- budgets ----------
class BudgetIn(BaseModel):
    category_id: int
    budget_month: date
    limit_amount: PositiveMoney

    @field_validator("budget_month")
    @classmethod
    def first_of_month(cls, v: date) -> date:
        return v.replace(day=1)


class BudgetUpdate(BaseModel):
    limit_amount: PositiveMoney


class BudgetCopyIn(BaseModel):
    from_month: date
    to_month: date


# ---------- goals ----------
class GoalIn(BaseModel):
    name: Name
    target_amount: PositiveMoney
    target_date: date | None = None
    account_id: int | None = None


class GoalUpdate(BaseModel):
    name: Name | None = None
    target_amount: PositiveMoney | None = None
    target_date: date | None = None
    account_id: int | None = None
    status: Literal["active", "achieved", "abandoned"] | None = None


class ContributionIn(BaseModel):
    amount: PositiveMoney
    from_account_id: int | None = None
    from_account_amount: PositiveMoney | None = None
    note: str | None = Field(default=None, max_length=200)


# ---------- portfolio ----------
class TradeIn(BaseModel):
    account_id: int
    security_id: int
    trade_type: Literal["buy", "sell"]
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
    price: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]
    fee: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)] = Decimal("0")
    trade_date: date | None = None
