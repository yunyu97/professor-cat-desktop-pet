"""
Professor Cat - Study edition.

Identical to ProfessorCatLife.py (same cat, Pomodoro timer, rest reminder, cat bed
and music controls) but it ships with NO default music. Right-click the cat and
choose "Add your study playlist..." to paste any YouTube playlist or video link;
it is remembered for next time.
"""
import ProfessorCatLife as cat_app

cat_app.EDITION.update(
    settings="Study",        # keeps its own saved playlist/volume, separate from Life
    default_url="",          # no built-in playlist
    study=True,
    title="Professor Cat Study",
)

if __name__ == "__main__":
    cat_app.main()
