"""Public contracts for the departmental office workspace."""

from typing import Annotated, Any, Literal

from pydantic import Field

from .common import Envelope, Model

Short = Annotated[str, Field(min_length=1, max_length=80)]


class Credentials(Model):
    username: Annotated[str, Field(min_length=3, max_length=40)]
    password: Annotated[str, Field(min_length=10, max_length=128)]


class Registration(Credentials):
    display_name: Short


class UserInfo(Model):
    id: str
    username: str
    display_name: str
    is_admin: bool
    disabled: bool
    must_change_password: bool


class AuthInfo(Envelope):
    user: UserInfo | None
    csrf_token: str
    initialized: bool
    limits: dict[str, int] = {"file_bytes": 20971520, "attachments": 5}


class PasswordChange(Model):
    current_password: str
    new_password: Annotated[str, Field(min_length=10, max_length=128)]


class UserUpdate(Model):
    disabled: bool | None = None
    password: Annotated[str | None, Field(min_length=10, max_length=128)] = None


class SkillRef(Model):
    id: str
    version: Annotated[int, Field(ge=1)]


class SkillContent(Model):
    title: Short
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    applicability: Annotated[str, Field(max_length=2000)] = ""
    inputs: Annotated[str, Field(max_length=2000)] = ""
    instructions: Annotated[str, Field(min_length=1, max_length=12000)]
    exceptions: Annotated[str, Field(max_length=2000)] = ""
    output_format: Annotated[str, Field(max_length=2000)] = ""
    tools: Annotated[list[str], Field(max_length=20)] = []


class AgentContent(Model):
    title: Short
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    instructions: Annotated[str, Field(max_length=8000)] = ""
    greeting: Annotated[str, Field(max_length=1000)] = ""
    examples: Annotated[list[Annotated[str, Field(max_length=300)]], Field(max_length=6)] = []
    skills: Annotated[list[SkillRef], Field(min_length=1, max_length=8)]
    tools: Annotated[list[str], Field(max_length=20)] = []


class SkillWrite(SkillContent):
    visibility: Literal["private", "public"] | None = None
    publish: bool = False


class AgentWrite(AgentContent):
    visibility: Literal["private", "public"] | None = None
    publish: bool = False


class ResourceInfo(Model):
    id: str
    kind: Literal["skill", "agent"]
    owner_id: str
    visibility: Literal["private", "public"]
    builtin: bool
    archived: bool
    published_version: int
    content: SkillContent | AgentContent
    editable: bool
    created_at: str


class ResourceList(Envelope):
    items: list[ResourceInfo]


class ResourceState(Model):
    visibility: Literal["private", "public"] | None = None
    archived: bool | None = None


class FileInfo(Model):
    id: str
    name: str
    mime: str
    size: int
    created_at: str
    download_url: str


class ConversationCreate(Model):
    agent_id: str
    version: int | None = None


class ConversationRename(Model):
    title: Short


class ConversationInfo(Model):
    id: str
    title: str
    agent_id: str
    agent_version: int
    agent_title: str
    greeting: str = ""
    examples: list[str] = []
    created_at: str


class ConversationList(Envelope):
    items: list[ConversationInfo]


class OfficeMessage(Model):
    id: str
    role: Literal["user", "assistant"]
    content: str
    files: list[FileInfo]
    execution_id: str | None
    created_at: str


class OfficeMessages(Envelope):
    items: list[OfficeMessage]


class MessageCreate(Model):
    content: Annotated[str, Field(min_length=1, max_length=12000)]
    attachment_ids: Annotated[list[str], Field(max_length=20)] = []


class ToolRun(Model):
    tool: str
    arguments: dict[str, Any]


class ToolInfo(Model):
    id: str
    title: str
    description: str
    parameters: dict[str, Any]
    enabled: bool


class ToolList(Envelope):
    items: list[ToolInfo]


class ToolState(Model):
    enabled: bool


class ExecutionInfo(Model):
    id: str
    kind: Literal["agent", "tool"]
    conversation_id: str | None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    created_at: str
    events: list[dict[str, Any]]
    files: list[FileInfo]
    result: dict[str, Any] | None
    error: str | None


class InternalOfficeCall(Model):
    context_token: str
    arguments: dict[str, Any]
