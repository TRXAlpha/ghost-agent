"""Tests for ghost_agent.pet — the Ghost pet emotion system."""
from ghost_agent.pet import GhostPet, PetState, EMOTIONS, phase_to_pet_state, result_to_pet_state


def test_pet_starts_idle() -> None:
    pet = GhostPet()
    assert pet.state == PetState.IDLE
    assert pet.get_emotion().face == "(-_^)"


def test_pet_set_state() -> None:
    pet = GhostPet()
    pet.set_state(PetState.WORKING)
    assert pet.state == PetState.WORKING
    assert pet.get_emotion().face == "(^_^)/"


def test_pet_render_inline() -> None:
    pet = GhostPet()
    pet.set_state(PetState.SUCCESS)
    rendered = pet.render_inline()
    assert "(*_*)" in rendered
    assert "done!" in rendered


def test_pet_render_box() -> None:
    pet = GhostPet()
    box = pet.render_box()
    assert "GHOST" in box
    assert "(-_^)" in box


def test_pet_render_compact() -> None:
    pet = GhostPet()
    compact = pet.render_compact()
    assert "(-_^)" in compact


def test_pet_idle_tick() -> None:
    pet = GhostPet()
    tick = pet.idle_tick()
    assert "(-_^)" in tick


def test_pet_state_changes() -> None:
    pet = GhostPet()
    pet.set_state(PetState.THINKING)
    assert pet.state == PetState.THINKING
    pet.set_state(PetState.WORKING)
    assert pet.state == PetState.WORKING
    pet.set_state(PetState.REPAIRING)
    assert pet.state == PetState.REPAIRING


def test_pet_same_state_no_change() -> None:
    pet = GhostPet()
    pet.set_state(PetState.IDLE)
    # Setting same state shouldn't change anything
    assert pet.state == PetState.IDLE


def test_all_emotions_exist() -> None:
    """Every PetState must have a corresponding emotion."""
    for state in PetState:
        assert state in EMOTIONS, f"Missing emotion for {state}"


def test_phase_to_pet_state() -> None:
    assert phase_to_pet_state("INGEST") == PetState.THINKING
    assert phase_to_pet_state("PLAN") == PetState.THINKING
    assert phase_to_pet_state("IMPLEMENT") == PetState.WORKING
    assert phase_to_pet_state("VERIFY") == PetState.THINKING
    assert phase_to_pet_state("REPAIR") == PetState.REPAIRING
    assert phase_to_pet_state("DONE") == PetState.SUCCESS
    assert phase_to_pet_state("UNKNOWN") == PetState.IDLE


def test_result_to_pet_state() -> None:
    assert result_to_pet_state("tests_passed") == PetState.SUCCESS
    assert result_to_pet_state("no_test_cmd") == PetState.SUCCESS
    assert result_to_pet_state("iteration_limit") == PetState.FAILURE
    assert result_to_pet_state("plan_failed") == PetState.FAILURE
    assert result_to_pet_state("implement_failed") == PetState.FAILURE
    assert result_to_pet_state("plan_ok") is None  # intermediate, not final
