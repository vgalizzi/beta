import pytest

from beta.core.step17 import func17


def test_5():
    assert func17(3) == 4


@pytest.mark.parametrize(
    ("entrée", "sortie"),
    [
        (0, 1),
        (-40, -39),  # le point où les deux échelles se croisent
        (36, 37),
    ],
)
def test_valeurs_connues(entrée, sortie):
    assert func17(entrée) == sortie
