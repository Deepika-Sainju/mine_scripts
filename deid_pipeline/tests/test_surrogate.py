from deid.surrogate.generator import SurrogateGenerator


def test_name_part_deterministic_across_instances():
    a = SurrogateGenerator(seed=1234).name_part("Marcus", "first", "M")
    b = SurrogateGenerator(seed=1234).name_part("Marcus", "first", "M")
    assert a == b


def test_name_part_seed_changes_fake():
    a = SurrogateGenerator(seed=1234).name_part("Marcus", "first", "M")
    b = SurrogateGenerator(seed=9999).name_part("Marcus", "first", "M")
    assert a != b


def test_name_part_gender_affects_first_name():
    gen = SurrogateGenerator(seed=1234)
    assert gen.name_part("Marcus", "first", "M") != gen.name_part("Marcus", "first", "F")


def test_name_part_gender_synonyms_normalize():
    gen = SurrogateGenerator(seed=1234)
    assert gen.name_part("Marcus", "first", "MALE") == gen.name_part("Marcus", "first", "M")


def test_name_part_preserves_casing():
    gen = SurrogateGenerator(seed=1234)
    assert gen.name_part("MARCUS", "first", "M").isupper()
    assert gen.name_part("marcus", "first", "M").islower()
    assert gen.name_part("Marcus", "first", "M")[0].isupper()


def test_middle_initial_stays_an_initial():
    gen = SurrogateGenerator(seed=1234)
    fake = gen.name_part("J", "middle")
    assert len(fake) == 1 and fake.isalpha() and fake.isupper()
    assert gen.name_part("J.", "middle").endswith(".")


def test_fake_dob_shifts_and_preserves_format():
    gen = SurrogateGenerator(seed=1234)
    fake = gen.fake_dob("03/14/1958")
    assert fake != "03/14/1958"
    assert len(fake) == 10 and fake[2] == "/" and fake[5] == "/"
    assert fake == SurrogateGenerator(seed=1234).fake_dob("03/14/1958")  # deterministic
    assert gen.fake_dob("not a date") == "not a date"


def test_identifier_format_preserving_and_deterministic():
    gen = SurrogateGenerator(seed=1234)
    fake = gen.identifier("H900000001")
    assert fake != "H900000001" and len(fake) == 10
    assert fake[0].isupper() and fake[1:].isdigit()
    assert fake == SurrogateGenerator(seed=1234).identifier("H900000001")
    assert gen.identifier("AB-12x")[2] == "-"
    assert gen.identifier("") == ""


def test_surrogate_caches_per_value():
    gen = SurrogateGenerator(seed=1234)
    assert gen.surrogate("ORG", "Acme Corp") == gen.surrogate("ORG", "Acme Corp")


def test_smart_keeps_structure_and_clinical_words():
    gen = SurrogateGenerator(seed=1234)
    out = gen.surrogate("PERSON", "History Hernandez lumbar")
    assert "Hernandez" not in out
    assert out.startswith("History ") and out.endswith(" lumbar")


def test_smart_shifts_embedded_dates():
    gen = SurrogateGenerator(seed=1234)
    out = gen.surrogate("PHI", "signed on 02/19/1958")
    assert out.startswith("signed on ") and out != "signed on 02/19/1958"
    assert len(out) == len("signed on 02/19/1958")  # date stays a date


def test_collision_guard_never_reuses_known_tokens():
    # the guard is armed at construction — there is no post-hoc arming step to forget
    fake = SurrogateGenerator(seed=1234).name_part("smith", "last")
    guarded = SurrogateGenerator(seed=1234, known_tokens={fake, "smith"})
    assert guarded.name_part("smith", "last").lower() != fake.lower()
