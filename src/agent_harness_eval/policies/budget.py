from pydantic import Field, model_validator

from ..workspace.models import Frozen


class Budget(Frozen):
    max_requests: int = Field(default=1000, gt=0, le=1000000)
    hard_cost_usd: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def enforceable(self):
        if self.hard_cost_usd is not None:
            raise ValueError("尚无法保证美元硬上限：服务端计费、重试费用和缺失 usage 不可预先确定；请使用请求数上限")
        return self
