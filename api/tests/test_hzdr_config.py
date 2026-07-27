import pytest
from fastapi.testclient import TestClient

from damnit_api.auth.ldap import _user_info_from_record
from damnit_api.main import create_app
from damnit_api.shared.hzdr_settings import HZDRLaserSettings
from damnit_api.shared.settings import DamnitSettings, LDAPSettings


def test_damnit_settings_resolves_hzdr_folder_path(tmp_path):
    """HZDR deployments can map a stable source key to a DAMNIT folder."""
    damnit_path = tmp_path / "hzdr-damnit"
    settings = DamnitSettings(paths_by_proposal={"hzdr": damnit_path})

    assert settings.path_for(proposal="hzdr") == damnit_path


def test_damnit_settings_prefers_explicit_path(tmp_path):
    """Explicit paths let starter deployments avoid proposal discovery."""
    configured_path = tmp_path / "configured"
    selected_path = tmp_path / "selected"
    settings = DamnitSettings(default_path=configured_path)

    assert settings.path_for(path=selected_path) == selected_path


def test_unconfigured_laser_settings_contribute_nothing():
    """An unconfigured deployment must build exactly the file it built before."""
    assert HZDRLaserSettings().as_metadata() == {}


def test_laser_settings_use_registry_bare_keys():
    """The block feeds metadata.laser.* directly, so its keys are the bare
    registry names with the registry's canonical units (nm, Hz)."""
    settings = HZDRLaserSettings(
        system="DRACO",
        wavelength=800.0,
        repetition_rate=1.0,
        polarization="p",
    )

    assert settings.as_metadata() == {
        "system": "DRACO",
        "wavelength": 800.0,
        "repetition_rate": 1.0,
        "polarization": "p",
    }


def test_laser_settings_reject_an_off_vocabulary_polarization():
    """A config typo is rejected rather than written into a certified file
    (producer metadata is only warned about — see lint_metadata_keys)."""
    settings = HZDRLaserSettings(polarization="p-pol @45deg")

    with pytest.raises(ValueError, match="not an accepted polarization label"):
        settings.as_metadata()


def test_ldap_record_is_converted_to_session_user():
    """LDAP auth returns the same session shape as OAuth userinfo."""
    settings = LDAPSettings()
    record = {
        "displayName": ["Ada Lovelace"],
        "mail": ["ada@example.org"],
        "givenName": ["Ada"],
        "sn": ["Lovelace"],
        "memberOf": ["cn=damnit-users,ou=groups,dc=example,dc=org"],
    }

    user_info = _user_info_from_record(settings, "alovelace", record)

    assert user_info["email"] == "ada@example.org"
    assert user_info["family_name"] == "Lovelace"
    assert user_info["given_name"] == "Ada"
    assert user_info["name"] == "Ada Lovelace"
    assert user_info["preferred_username"] == "alovelace"
    assert user_info["groups"] == ["cn=damnit-users,ou=groups,dc=example,dc=org"]


def test_app_starts_without_mymdc():
    """HZDR/local deployments should not require MyMdC bootstrap."""
    import damnit_api._mymdc

    damnit_api._mymdc.CLIENT = None
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert damnit_api._mymdc.CLIENT is None
