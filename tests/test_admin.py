"""Admin tests — ShiftTemplate CRUD, AssignedShift CRUD, SystemConfig.

Covers the shift-templates, turn-assignment and traceability (SystemConfig)
delta specs. Uses the authenticated role fixtures from conftest.
"""

from app.models import (
    AssignedShift,
    AttendanceEvent,
    ShiftTemplate,
    SystemConfig,
    User,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _create_user(username, role="funcionario", supervisor=None):
    from app.services.security import hash_password

    return User.create(
        username=username,
        email=f"{username}@test.local",
        name=f"Usuario {username}",
        role=role,
        password_hash=hash_password("test1234"),
        must_change_password=False,
        is_active=True,
        supervisor=supervisor,
    )


def _create_template(name="Turno Mañana", start="06:00", end="14:00",
                     mask="1111100"):
    return ShiftTemplate.create(
        name=name, start_time=start, end_time=end, weekday_mask=mask
    )


def _post_template(client, name, start, end, mask="1111100"):
    return client.post(
        "/admin/plantillas",
        data={
            "name": name,
            "start_time": start,
            "end_time": end,
            "weekday_mask": mask,
        },
        follow_redirects=True,
    )


# ── ShiftTemplate CRUD (task 2.1) ─────────────────────────────────────


class TestShiftTemplateCRUD:
    def test_create_template_persists_and_appears_in_list(self, auth_admin):
        client, _ = auth_admin
        response = _post_template(
            client, "Turno Mañana", "06:00", "14:00"
        )

        assert response.status_code == 200
        template = ShiftTemplate.get_or_none(
            ShiftTemplate.name == "Turno Mañana"
        )
        assert template is not None
        assert template.start_time == "06:00"
        assert template.end_time == "14:00"
        assert template.weekday_mask == "1111100"
        assert template.is_active is True
        assert "Turno Mañana" in response.data.decode()

    def test_create_template_duplicate_name_rejected(self, auth_admin):
        client, _ = auth_admin
        _post_template(client, "Turno Mañana", "06:00", "14:00")
        response = _post_template(client, "Turno Mañana", "07:00", "15:00")

        assert "Nombre de plantilla duplicado" in response.data.decode()
        count = ShiftTemplate.select().where(
            ShiftTemplate.name == "Turno Mañana"
        ).count()
        assert count == 1

    def test_same_start_end_rejected(self, auth_admin):
        client, _ = auth_admin
        response = _post_template(client, "Raro", "08:00", "08:00")

        assert "Horario inválido" in response.data.decode()
        assert ShiftTemplate.get_or_none(ShiftTemplate.name == "Raro") is None

    def test_overnight_shift_is_valid(self, auth_admin):
        client, _ = auth_admin
        response = _post_template(client, "Turno Noche", "22:00", "06:00")

        assert response.status_code == 200
        template = ShiftTemplate.get_or_none(
            ShiftTemplate.name == "Turno Noche"
        )
        assert template is not None
        assert template.is_overnight

    def test_delete_template_rejected_with_active_assignments(self, auth_admin):
        client, _ = auth_admin
        worker = _create_user("carlos")
        template = _create_template()
        AssignedShift.create(user=worker, template=template)

        response = client.post(
            f"/admin/plantillas/{template.id}/eliminar",
            follow_redirects=True,
        )

        assert (
            "No se puede eliminar: tiene asignaciones activas"
            in response.data.decode()
        )
        assert ShiftTemplate.get_or_none(ShiftTemplate.id == template.id)

    def test_delete_template_without_assignments(self, auth_admin):
        client, _ = auth_admin
        template = _create_template()

        response = client.post(
            f"/admin/plantillas/{template.id}/eliminar",
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert ShiftTemplate.get_or_none(
            ShiftTemplate.id == template.id
        ) is None

    def test_deactivate_template_hides_from_new_assignments_only(
        self, auth_admin
    ):
        client, _ = auth_admin
        worker = _create_user("carlos")
        template = _create_template(name="Turno Noche", start="22:00",
                                    end="06:00")
        AssignedShift.create(user=worker, template=template)

        response = client.post(
            f"/admin/plantillas/{template.id}/toggle", follow_redirects=True
        )
        assert response.status_code == 200
        template = ShiftTemplate.get_by_id(template.id)
        assert template.is_active is False

        page = client.get("/admin/asignaciones").data.decode()
        # Inactive template is NOT an option in the new-assignment form
        assert f'<option value="{template.id}"' not in page
        # Existing assignment referencing it remains listed
        assert "Turno Noche" in page


# ── AssignedShift CRUD (task 2.2) ─────────────────────────────────────


class TestAssignedShiftCRUD:
    def test_assign_template_to_user(self, auth_admin):
        client, _ = auth_admin
        worker = _create_user("carlos")
        template = _create_template(name="Turno Mañana", start="06:00",
                                    end="14:00", mask="1111100")

        response = client.post(
            "/admin/asignaciones",
            data={"user_id": worker.id, "template_id": template.id},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assignment = AssignedShift.get(
            AssignedShift.user == worker, AssignedShift.template == template
        )
        assert assignment.is_active is True
        assert template.weekday_mask == "1111100"
        assert "Turno Mañana" in response.data.decode()

    def test_multiple_shifts_same_day_coexist(self, auth_admin):
        client, _ = auth_admin
        worker = _create_user("carlos")
        morning = _create_template(name="Turno Mañana", start="06:00",
                                   end="14:00")
        afternoon = _create_template(name="Turno Tarde", start="14:00",
                                     end="22:00")
        AssignedShift.create(user=worker, template=morning)
        AssignedShift.create(user=worker, template=afternoon)

        count = AssignedShift.select().where(
            AssignedShift.user == worker
        ).count()
        assert count == 2

    def test_supervisor_sees_own_team_only(self, auth_supervisor):
        client, supervisor = auth_supervisor
        carlos = _create_user("carlos", supervisor=supervisor)
        luis = _create_user("luis", supervisor=supervisor)
        other_supervisor = _create_user("bob", role="supervisor")
        elena = _create_user("elena", supervisor=other_supervisor)

        morning = _create_template()
        AssignedShift.create(user=carlos, template=morning)
        AssignedShift.create(user=luis, template=morning)
        AssignedShift.create(user=elena, template=morning)

        page = client.get("/admin/asignaciones").data.decode()
        assert "carlos" in page
        assert "luis" in page
        assert "elena" not in page

    def test_supervisor_cannot_view_other_team(self, auth_supervisor):
        client, supervisor = auth_supervisor
        other_supervisor = _create_user("bob", role="supervisor")
        elena = _create_user("elena", supervisor=other_supervisor)

        response = client.get(f"/admin/asignaciones/{elena.id}")
        assert response.status_code == 403

    def test_supervisor_can_view_own_team_member(self, auth_supervisor):
        client, supervisor = auth_supervisor
        carlos = _create_user("carlos", supervisor=supervisor)

        response = client.get(f"/admin/asignaciones/{carlos.id}")
        assert response.status_code == 200

    def test_admin_can_view_any_user(self, auth_admin):
        client, _ = auth_admin
        elena = _create_user("elena")

        response = client.get(f"/admin/asignaciones/{elena.id}")
        assert response.status_code == 200

    def test_remove_assignment_preserves_past_events(self, auth_admin):
        client, _ = auth_admin
        worker = _create_user("carlos")
        template = _create_template()
        assignment = AssignedShift.create(user=worker, template=template)
        event = AttendanceEvent.create(
            user=worker,
            event_type="entry",
            source="qr",
            outcome="OK",
            shift=assignment,
        )

        response = client.post(
            f"/admin/asignaciones/{assignment.id}/eliminar",
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert AssignedShift.get_or_none(
            AssignedShift.id == assignment.id
        ) is None
        # Past events remain, with the shift reference cleared
        event = AttendanceEvent.get_by_id(event.id)
        assert event.shift_id is None

    def test_funcionario_cannot_create_assignment(self, auth_funcionario):
        client, _ = auth_funcionario
        worker = _create_user("carlos")
        template = _create_template()

        response = client.post(
            "/admin/asignaciones",
            data={"user_id": worker.id, "template_id": template.id},
            follow_redirects=False,
        )
        assert response.status_code == 403


# ── SystemConfig management (task 2.3) ────────────────────────────────


class TestSystemConfig:
    def test_admin_updates_late_grace_minutes(self, auth_admin):
        client, _ = auth_admin
        client.post(
            "/admin/configuracion",
            data={"key": "late_grace_minutes", "value": "10"},
        )
        row = SystemConfig.get_or_none(
            SystemConfig.key == "late_grace_minutes"
        )
        assert row is not None
        assert row.value == "10"

    def test_admin_toggles_kiosk_enabled(self, auth_admin):
        client, _ = auth_admin
        client.post(
            "/admin/configuracion",
            data={"key": "kiosk_enabled", "value": "false"},
        )
        row = SystemConfig.get_or_none(SystemConfig.key == "kiosk_enabled")
        assert row is not None
        assert row.value == "false"

    def test_config_page_lists_all_defaults(self, auth_admin):
        client, _ = auth_admin
        for key, value in (
            ("kiosk_enabled", "true"),
            ("late_grace_minutes", "5"),
            ("qr_validity_seconds", "45"),
            ("timezone", "UTC"),
        ):
            client.post(
                "/admin/configuracion",
                data={"key": key, "value": value},
            )

        page = client.get("/admin/configuracion").data.decode()
        assert "kiosk_enabled" in page
        assert "late_grace_minutes" in page
        assert "qr_validity_seconds" in page
        assert "timezone" in page

    def test_config_update_admin_only(self, auth_funcionario):
        client, _ = auth_funcionario
        response = client.post(
            "/admin/configuracion",
            data={"key": "late_grace_minutes", "value": "10"},
        )
        assert response.status_code == 403