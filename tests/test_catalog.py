"""Legacy filesystem import and built-in catalog compatibility after account migration."""
from pathlib import Path

from packages.adapters.catalog import move_skill, read_catalog, register_skill
from packages.adapters.office_resources import ResourceService
from packages.adapters.platform_store import Platform
from packages.adapters.storage import Store, chat_sessions
from packages.contracts.api import SkillRegistration
from packages.contracts.common import now, uid

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_reflects_protected_runtime_agents_and_tools():
    catalog = read_catalog(ROOT)
    assert {a.name for a in catalog.agents} == {'reception', 'monitor', 'control', 'office', 'st-code'}
    assert next(a for a in catalog.agents if a.name == 'st-code').tools == []
    assert {t.name for t in catalog.tools} == {'get_recent_window', 'calculate_metrics', 'detect_state', 'get_tuning_context'}
    assert next(s for s in catalog.skills if s.name == 'pid-tuning').agents == ['control']


def test_legacy_skill_import_preserves_file_and_assigns_admin(tmp_path):
    catalog = tmp_path / 'catalog'
    request = SkillRegistration(schema_version='1.0', title='原有技能', description='迁移前的流程', triggers=['原有流程'], instructions='保留这些步骤。')
    skill = register_skill(catalog, request)
    path = catalog / '.opencode/skills' / skill.name / 'SKILL.md'
    original = path.read_bytes()
    db = Store(tmp_path / 'state')
    try:
        p = Platform(db, catalog)
        user = p.initialize_admin('admin', '管理员', 'migration-test-password')
        service = ResourceService(p)
        imported = service.get('legacy-' + skill.name, user['id'])
        assert imported.owner_id == user['id'] and imported.visibility == 'private'
        assert '保留这些步骤' in imported.content.instructions
        assert path.read_bytes() == original
        service.update(imported.id, user['id'], {**imported.content.model_dump(), 'title': '修改后的标题'})
        p.import_legacy(user['id'])
        assert service.get(imported.id, user['id']).content.title == '修改后的标题'
    finally:
        db.close()


def test_legacy_chat_migrates_once(tmp_path):
    db = Store(tmp_path / 'state')
    try:
        session_id = uid()
        with db.engine.begin() as c:
            c.execute(chat_sessions.insert().values(id=session_id, user_id='local-developer', title='旧对话', created_at=now()))
        platform = Platform(db, tmp_path / 'catalog')
        user = platform.initialize_admin('admin', '管理员', 'migration-test-password')
        with db.engine.connect() as c:
            assert c.execute(chat_sessions.select()).mappings().one()['user_id'] == user['id']
        platform.import_legacy(user['id'])
    finally:
        db.close()


def test_archived_legacy_skill_stays_archived(tmp_path):
    catalog = tmp_path / 'catalog'
    skill = register_skill(catalog, SkillRegistration(schema_version='1.0', title='归档流程', description='旧流程', triggers=['归档'], instructions='已归档。'))
    move_skill(catalog, skill.name)
    db = Store(tmp_path / 'state')
    try:
        p = Platform(db, catalog)
        user = p.initialize_admin('admin', '管理员', 'migration-test-password')
        imported = ResourceService(p).get('legacy-' + skill.name, user['id'])
        assert imported.archived
    finally:
        db.close()
