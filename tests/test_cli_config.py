import pytest

from faangscout.cli import apply_config, build_parser, main


def parse(tmp_path, config_text, *argv):
    cfg = tmp_path / "scout.yaml"
    cfg.write_text(config_text)
    args = build_parser().parse_args(["--config", str(cfg), *argv])
    apply_config(args)
    return args


def test_config_fills_unset_args(tmp_path):
    args = parse(tmp_path, "companies: [Stripe, Amazon]\nrole: software engineer\nhours: 48\n")
    assert args.companies == ["Stripe", "Amazon"]
    assert args.role == "software engineer"
    assert args.hours == 48.0


def test_command_line_wins_over_config(tmp_path):
    args = parse(tmp_path, "companies: [Stripe]\nrole: sde\nhours: 48\n",
                 "--companies", "Airbnb", "--role", "backend", "--hours", "12")
    assert args.companies == ["Airbnb"]
    assert args.role == "backend"
    assert args.hours == 12.0


def test_hours_default_without_config():
    args = build_parser().parse_args(["--companies", "Stripe"])
    apply_config(args)
    assert args.hours == 24.0


def test_duplicate_companies_removed_case_insensitively(tmp_path):
    args = parse(tmp_path, "companies: [Stripe, Amazon, stripe]\n")
    assert args.companies == ["Stripe", "Amazon"]


def test_comma_separated_string_accepted(tmp_path):
    args = parse(tmp_path, "companies: 'Stripe, Amazon'\n")
    assert args.companies == ["Stripe", "Amazon"]


def test_no_companies_anywhere_is_an_error(tmp_path, capsys):
    cfg = tmp_path / "scout.yaml"
    cfg.write_text("role: sde\n")
    with pytest.raises(SystemExit):
        main(["--config", str(cfg)])
    assert "no companies given" in capsys.readouterr().err


def test_non_mapping_config_is_an_error(tmp_path, capsys):
    cfg = tmp_path / "scout.yaml"
    cfg.write_text("- just\n- a list\n")
    with pytest.raises(SystemExit):
        main(["--config", str(cfg)])
    assert "must contain a YAML mapping" in capsys.readouterr().err


def test_config_location_and_experience(tmp_path):
    args = parse(tmp_path, "companies: [Stripe]\nlocation: India\nexperience: 3\n")
    assert args.location == "India"
    assert args.experience == 3.0


def test_config_experience_dict_form(tmp_path):
    args = parse(tmp_path, "companies: [Stripe]\nexperience: {years: 4}\n")
    assert args.experience == 4.0
