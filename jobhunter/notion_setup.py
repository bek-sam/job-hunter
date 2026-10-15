"""Create the Notion databases Job Hunter expects, inside one page you share with your integration.

  1. notion.so/my-integrations -> New integration -> copy the secret into .env as NOTION_TOKEN
  2. In Notion create an empty page (e.g. "Jobs 2026") -> ... -> Connections -> add your integration
  3. .venv/bin/python jobhunter/notion_setup.py <page link or id>
     -> creates Applications, Experience, Projects, Reports and Community Job Board under that page
        and prints the NOTION_* lines to paste into .env
  --dry-run prints what would be created without calling Notion.
"""
import argparse, json, re, sys
import config

SEL = lambda *names: {'select': {'options': [{'name': n} for n in names]}}
MULTI = lambda *names: {'multi_select': {'options': [{'name': n} for n in names]}}
TXT, DATE, URL, NUM = {'rich_text': {}}, {'date': {}}, {'url': {}}, {'number': {}}
VIA = ('LinkedIn Easy Apply', 'LinkedIn', 'Workday', 'Greenhouse', 'Ashby', 'Lever', 'iCIMS', 'SmartRecruiters',
       'SuccessFactors', 'Oracle', 'Taleo', 'Eightfold', 'Jobvite', 'Workable', 'Company website')
BEST_FOR = ('AI/ML', 'Backend', 'Full-stack', 'Frontend', 'iOS/Mobile', 'Data', 'DevOps/Cloud', 'Security', 'Automation')

DBS = [  # (env key, title, title property, other properties)
    ('NOTION_APPS_DS', 'Applications', 'Company', {
        'Position': TXT, 'Status': SEL('To apply', 'Needs you', 'Applied', 'OA', 'Interview', 'Offer', 'Rejected', 'Ghosted', 'Closed'),
        'Notes': TXT, 'Link': URL, 'Location': TXT, 'Date Posted': DATE, 'Date Applied': DATE, 'Last Contact': DATE,
        'Apply Via': SEL(*VIA), 'Outreach': SEL('Sent', 'Follow-up 1', 'Follow-up 2', 'Replied', 'No contact found'),
        'Recruiters': TXT, 'LinkedIn': SEL('Queued', 'Invited', 'Connected', 'Messaged', 'Replied')}),
    ('NOTION_EXPERIENCE_DS', 'Experience (resume bullet bank)', 'Name', {
        'Company': TXT, 'Role': TXT, 'Location': TXT, 'Dates': TXT, 'Type': SEL('Internship', 'Full-time', 'Teaching', 'Leadership'),
        'Tech': TXT, 'Key numbers': TXT, 'Repos / links': TXT, 'Best for': MULTI(*BEST_FOR), 'Order': NUM}),
    ('NOTION_PROJECTS_DS', 'Projects (resume bullet bank)', 'Name', {
        'Role': TXT, 'Dates': TXT, 'Kind': SEL('Startup / product', 'Work-related', 'Personal', 'Hackathon', 'Team project', 'Class project'),
        'Tech': TXT, 'Key numbers': TXT, 'Repos / links': TXT, 'Best for': MULTI(*BEST_FOR), 'Order': NUM}),
    ('NOTION_REPORTS_DS', 'Reports', 'Name', {
        'Date': DATE, 'Kind': SEL('Manager Report', 'Job Search Log', 'Apply Now'), 'Slot': SEL('early', 'evening', 'night', 'weekly', 'daily'),
        'Source': SEL('local'), 'Archived': {'checkbox': {}}}),
    ('NOTION_COMMUNITY_DS', 'Community Job Board', 'Company', {
        'Job Title': TXT, 'Type': SEL('Internship', 'New Grad', 'Full-time', 'Contract'), 'Location': TXT, 'Date Posted': DATE,
        'Apply Link': URL, 'Apply Via': SEL(*VIA), 'Added': {'created_time': {}}}),
]


def page_id(s):
    m = re.search(r'([0-9a-f]{32})(?:[?#].*)?$', s.replace('-', '')) or re.search(r'([0-9a-f]{32})', s.replace('-', ''))
    if not m: sys.exit(f'not a Notion page link or id: {s}')
    h = m.group(1)
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}'


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('page'); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    parent = page_id(a.page)
    if not a.dry_run and not config.NOTION_TOKEN: sys.exit('NOTION_TOKEN is empty in .env')
    import notion
    lines = [f'NOTION_JOBS_HUB={parent}']
    for env, title, title_prop, props in DBS:
        body = {'parent': {'type': 'page_id', 'page_id': parent}, 'title': [{'type': 'text', 'text': {'content': title}}],
                'initial_data_source': {'properties': {title_prop: {'title': {}}, **props}}}
        if a.dry_run:
            print(f'would create "{title}": {", ".join([title_prop] + list(props))}'); continue
        db = notion._req('POST', '/databases', body)
        ds = (db.get('data_sources') or [{}])[0].get('id')
        if not ds: sys.exit(f'created {title} but got no data source id: {json.dumps(db)[:300]}')
        print(f'created "{title}"'); lines.append(f'{env}={ds}')
    if not a.dry_run:
        print('\nPaste into .env:\n' + '\n'.join(lines))


if __name__ == '__main__':
    main()
