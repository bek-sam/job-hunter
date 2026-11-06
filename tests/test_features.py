"""Tests for the resume / proof / report features: no browser, no Notion, no LLM, no LibreOffice."""
import datetime as dt, importlib, json, os, pathlib, sys, tempfile, unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'jobhunter'))
os.environ.setdefault('PROFILE', str(ROOT / 'profile.example.json'))

import config  # noqa: E402
TMP = pathlib.Path(tempfile.mkdtemp(prefix='jh_feat_')); config.DATA = TMP

import applicant as P, reword, resume_check as RC, packet, reports  # noqa: E402


def make_pdf(lines, path):
    """Tiny text PDF (Helvetica) with one line per entry -- enough for pypdf text extraction."""
    import pikepdf
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    esc = lambda s: s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    ops = 'BT /F1 9 Tf 40 760 Td 11 TL ' + ' '.join(f'({esc(l)}) Tj T*' for l in lines) + ' ET'
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
                              Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                              Contents=pdf.make_stream(ops.encode('latin-1')))
    pdf.pages.append(pikepdf.Page(page)); pdf.save(path)
    return path


RESUME = [P.NAME, f'City | {P.EMAIL} | linkedin', 'EDUCATION', 'State University, B.S. Computer Science',
          'EXPERIENCE', 'Software Engineer Intern - Built REST APIs in Python and PostgreSQL serving 2,000 users.',
          'PROJECTS', 'Wardrobe app - React, TypeScript, Docker; cut load time 40%.',
          'TECHNICAL SKILLS', 'Languages: Python, TypeScript, SQL', 'Frameworks: React, FastAPI'] + ['filler text line'] * 30


class Reword(unittest.TestCase):
    allowed = reword.vocabulary({'required': ['PostgreSQL', 'REST APIs'], 'jd_text': 'We use PostgreSQL and REST APIs.'})

    def test_synonym_swap_ok(self):
        self.assertTrue(reword.valid('Built REST services on Postgres for 2,000 users',
                                     'Built REST APIs on PostgreSQL for 2,000 users', self.allowed))

    def test_number_change_rejected(self):
        self.assertFalse(reword.valid('Cut latency 40% on Postgres', 'Cut latency 60% on PostgreSQL', self.allowed))

    def test_new_claim_rejected(self):   # "Kubernetes" is not in the job's vocabulary -> not allowed
        self.assertFalse(reword.valid('Built REST services on Postgres', 'Built REST services on Kubernetes', self.allowed))

    def test_off_switch(self):
        with mock.patch.dict(os.environ, {'BULLET_REWORD': '0'}):
            self.assertEqual(reword.plan({'required': ['X']}, ['a bullet']), {})


class ResumeCheck(unittest.TestCase):
    def test_good_pdf_passes_and_reports_gaps(self):
        pdf = make_pdf(RESUME, str(TMP / 'good.pdf'))
        txt = RC.pdf_text(pdf)
        self.assertEqual(RC.parse_check(pdf, txt), [])
        rep = RC.match_report(txt, ['Python', 'PostgreSQL', 'Kubernetes'], ['React', 'Go'])
        self.assertEqual(rep['missing_required'], ['Kubernetes'])
        self.assertEqual(rep['required_pct'], 67)
        self.assertEqual(rep['missing_preferred'], ['Go'])

    def test_missing_email_and_order(self):
        lines = [P.NAME, 'EXPERIENCE', 'x', 'EDUCATION', 'PROJECTS', 'TECHNICAL SKILLS'] + ['filler text line'] * 40
        errs = RC.parse_check(make_pdf(lines, str(TMP / 'bad.pdf')))
        self.assertTrue(any('email' in e for e in errs)); self.assertTrue(any('order' in e for e in errs))

    def test_stuffing_flag(self):
        rep = RC.match_report('python ' * 5, ['Python'], [])
        self.assertEqual(rep['stuffed'], ['Python x5'])


class TitleLine(unittest.TestCase):
    def test_off_by_default_and_cleaned(self):
        import gen_v6
        with mock.patch.dict(os.environ, {'RESUME_TITLE_LINE': '0'}):
            self.assertIsNone(gen_v6.title_line({'position': 'Software Engineer, New Grad'}))
        with mock.patch.dict(os.environ, {'RESUME_TITLE_LINE': '1'}):
            self.assertEqual(gen_v6.title_line({'position': 'Software Engineer, New Grad (Remote)'}), 'Software Engineer, New Grad')
            self.assertEqual(gen_v6.title_line({'position': 'Backend Engineer - Austin, TX'}), 'Backend Engineer')

    def test_required_terms_count_double(self):
        import gen_v6
        t = gen_v6._terms({'required': ['Python'], 'preferred': ['Go']})
        b = {'text': 'Built a Python service with Go workers handling 3 queues and retries for the platform team', 'tags': []}
        b2 = {'text': 'Built a Rust service with Go workers handling 3 queues and retries for the platform team', 'tags': []}
        self.assertGreater(gen_v6._score(b, 'general', t, 999), gen_v6._score(b2, 'general', t, 999))


class CoverLetter(unittest.TestCase):
    def test_rules_loaded_from_skill(self):
        self.assertIn('Banned phrases', packet.RULES)
        self.assertIn('COVER LETTER RULES', packet.SYSTEM)

    def test_stock_phrase_triggers_one_rewrite_and_cache(self):
        bad = {'team': 't', 'what_they_value': 'v', 'cover_letter': 'I am excited to apply. I am passionate about code.'}
        good = dict(bad, cover_letter='Your payments team ships weekly. I built X.')
        role = {'url': 'https://job-boards.greenhouse.io/acme/jobs/77', 'title': 'SWE', 'company': 'Acme'}
        with mock.patch.object(packet, '_write', side_effect=[bad, good]) as w:
            out = packet.write(role, 'jd', {}, 'newgrad')
            self.assertEqual(out['cover_letter'], good['cover_letter']); self.assertEqual(w.call_count, 2)
            self.assertEqual(packet.write(role, 'jd', {}, 'newgrad')['cover_letter'], good['cover_letter'])   # cached
            self.assertEqual(w.call_count, 2)


class Reports(unittest.TestCase):
    def test_review_groups(self):
        rows = [{'Status': 'OA', 'Apply Via': 'Greenhouse', 'Notes': 'FAST | fit 88 | cov 92% | resume v6.2', 'Company': 'A'},
                {'Status': 'Applied', 'Apply Via': 'Greenhouse', 'Notes': 'FAST | fit 72 | cov 40% | resume v6.2', 'Company': 'B'},
                {'Status': 'Rejected', 'Apply Via': 'Ashby', 'Notes': 'FAST | INTERN | fit 80 | resume v6.1', 'Company': 'C'}]
        t = reports.review_text(rows)
        self.assertIn('3 applications · OA/interview/offer 1 (33%) · any answer 2 (66%)', t)
        self.assertIn('Greenhouse: 2 applied · 1 OA+ (50%)', t)
        self.assertIn('90%+: 1 applied', t)

    def test_daily_text_counts(self):
        now = dt.datetime.now(config.TZ).isoformat()
        (config.DATA / 'apply_results.jsonl').write_text('\n'.join(json.dumps(x) for x in [
            {'at': now, 'status': 'SUBMITTED', 'company': 'A', 'ats': 'greenhouse'},
            {'at': now, 'status': 'NEEDS YOU', 'company': 'B', 'reason': 'CAPTCHA'},
            {'at': now, 'status': 'SKIPPED', 'company': 'C', 'reason': 'auto-apply is limited to greenhouse/ashby'}]) + '\n')
        (config.DATA / 'submissions.jsonl').write_text(json.dumps({'at': now, 'company': 'A', 'role': 'SWE', 'ats': 'greenhouse',
                                                           'row': 'abc', 'proof': ['x.png']}) + '\n')
        text, sent, need = reports.daily_text()
        self.assertEqual((sent, need), (1, 1)); self.assertIn('proof screenshot', text)


class Proof(unittest.TestCase):
    def test_screenshot_path_and_failure_safe(self):
        import apply_prep
        page = mock.Mock()
        f = apply_prep.proof_shot(page, {'id': 'row-1'}, 'filled')
        self.assertTrue(str(f).endswith('-filled.png')); page.screenshot.assert_called_once()
        page.screenshot.side_effect = RuntimeError('closed')
        self.assertIsNone(apply_prep.proof_shot(page, {'id': 'row-1'}, 'after-submit'))


if __name__ == '__main__':
    unittest.main()
