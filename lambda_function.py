import json
import mimetypes
import time
from datetime import datetime

import boto3
import jwt
import openpyxl
import requests
from botocore.exceptions import ClientError
from dateutil.relativedelta import relativedelta

GIT_API_BASE_URL = 'https://api.github.com'
API_BASE_URL = "https://public-api.eu.drata.com"
OWNER_ID = 'your_owner_id'
SOURCE_TYPE = 'your_source_type'
RENEWAL_SCHEDULE_TYPE = 'your_renewal_schedule_type'
FILE_SAVE_BASE_PATH = '/tmp/'


def lambda_handler(event, context):
    token = get_token_git_app()
    config_json = [{"owner": "malinda-peiris", "project": "facemymelody", "branch": "main", "workspace_id": 12323, " evidence_id": 234234}]
    for config in config_json:
        repo_details = get_branch_config_details(token, config)
        pr_logs = fetch_pull_requests(token, config)
        create_xlsx_update_evidence(repo_details, pr_logs, config)
    return {
        'statusCode': 200,
        'body': json.dumps('File upload completed...')
    }


def get_branch_config_details(token, config):
    owner = config.get('owner')
    project = config.get('project')
    branch = config.get('branch')

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.luke-cage-preview+json"
    }

    # Get branch details
    branch_url = f"{GIT_API_BASE_URL}/repos/{owner}/{project}/branches/{branch}"
    branch_response = requests.get(branch_url, headers=headers)
    print(f'branch details extracted with status code:{branch_response.status_code}\n')

    branch_info = branch_response.json()
    # Get branch protection rules
    protection_url = f"{GIT_API_BASE_URL}/repos/{owner}/{project}/branches/{branch}/protection"
    protection_response = requests.get(protection_url, headers=headers)
    print(f'protection_info extracted with status code:{protection_response.status_code}\n')
    protection_info = protection_response.json()

    # Extract required protection details
    required_reviews = protection_info.get('required_pull_request_reviews', {})
    required_approving_review_count = required_reviews.get('required_approving_review_count', 0)
    require_code_owner_reviews = required_reviews.get('require_code_owner_reviews', False)
    allow_deletions_info = protection_info.get('allow_deletions', {})
    allow_deletions = allow_deletions_info.get('enabled', False)
    allow_force_pushes_info = protection_info.get('allow_force_pushes', {})
    allow_force_pushes = allow_force_pushes_info.get('enabled', False)

    # Return results
    return json.dumps({
        'branch_name': branch_info.get('name'),
        'repo_name': project,
        'required_reviews': required_approving_review_count,
        'reset_on_source_push': False,
        'require_code_owner_reviews': require_code_owner_reviews,
        'allow_deletion': allow_deletions,
        'allow_force_pushes': allow_force_pushes,
        'branch_is_protected': branch_info.get('protected')
    })


def fetch_pull_requests(token, config, state='all'):
    owner = config.get('owner')
    repo = config.get('project')

    current_time = datetime.now()
    one_month_ago = current_time - relativedelta(months=1)
    print("Pull requests extracted from "+current_time.strftime('%Y-%m-%d %H:%M:%S')+" to "+one_month_ago.strftime('%Y-%m-%d %H:%M:%S'))
    until = current_time
    since = one_month_ago

    url = f"{GIT_API_BASE_URL}/repos/{owner}/{repo}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    params = {
        'state': state,
        'per_page': 100,  # Max results per page
        'page': 1
    }

    pull_requests = []

    while True:
        response = requests.get(url, headers=headers, params=params)
        response_data = response.json()
        if not response_data:
            break

        for pr in response_data:

            created_at = datetime.strptime(pr['created_at'], '%Y-%m-%dT%H:%M:%SZ')

            if (since and created_at < since) or (until and created_at > until):
                continue

            author_json = pr.get('user')
            head_json = pr.get('head')
            head_repo_json = head_json.get('repo')

            base_json = pr.get('base')
            base_repo_json = base_json.get('repo')
            merge_user_json = pr.get('user')

            commit_message = get_commit_message(token, owner, repo, pr.get('merge_commit_sha'))
            latest_reviews = get_latest_reviews(token, owner, repo, pr.get('number'))
            latest_comments = get_latest_comments(token, pr.get('review_comments_url'))

            all_comments = ''
            for comment in latest_comments:
                all_comments += comment['body'] + "\n"

            all_reviews = ''
            for review in latest_reviews:
                submitted_at = '' if review['state'] == 'PENDING' else review['submitted_at']
                all_reviews += (
                    f"{review['state']} By {review['author_association']} {review['user']['login']} ({review['user']['url']}) on {submitted_at}\n")

            pull_requests.append({
                'number': pr['number'],
                'title': pr['title'],
                'url': pr['url'],
                'body': pr['body'],
                'created_at': pr['created_at'],
                'updated_at': pr['updated_at'],
                'author': author_json['login'],
                'author_url': author_json['url'],
                'head_ref': head_json['ref'],
                'head_repo': head_repo_json['full_name'],
                'merge_by': merge_user_json['login'],
                'merge_user_url': merge_user_json['url'],
                'merged_at': pr['merged_at'],
                'target_ref': base_json['ref'],
                'target_repo': base_repo_json['full_name'],
                'commit_message': commit_message,
                'latest_reviews': all_reviews,
                'latest_comments': all_comments,
                'state': pr['state']
            })

        params['page'] += 1
    print(f'Total number of pull requests extracted is:{len(pull_requests)}')
    return json.dumps(pull_requests)


def get_commit_message(token, owner, repo, commit_sha):
    url = f"{GIT_API_BASE_URL}/repos/{owner}/{repo}/commits/{commit_sha}"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    # Make the API request
    response = requests.get(url, headers=headers)

    # Check if the request was successful
    if response.status_code == 200:
        commit_data = response.json()
        commit_message = commit_data['commit']['message']
        return commit_message
    else:
        return f'Error in get_commit_message: {response.status_code} - {response.text}\n'


def get_latest_reviews(token, owner, repo, pull_number):
    url = f"{GIT_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/reviews"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    response = requests.get(url, headers=headers)

    # Check if the request was successful
    if response.status_code == 200:
        return response.json()
    else:
        return f"Error in get_latest_reviews: {response.status_code} - {response.text}"


def get_latest_comments(token, url):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    response = requests.get(url, headers=headers)

    # Check if the request was successful
    if response.status_code == 200:
        comments = response.json()
        return comments
    else:
        return f"Error: {response.status_code} - {response.text}"


def create_xlsx_update_evidence(repo_details, pr_logs, config):
    repo_json = json.loads(repo_details)
    workbook = openpyxl.Workbook()
    sheet1 = workbook.active
    sheet1.title = "Config"
    sheet1.column_dimensions['A'].width = 35
    sheet1.column_dimensions['B'].width = 15
    # Add headers to the columns
    sheet1['A1'] = 'Branch Details'

    sheet1['A3'] = 'Branch Name'
    sheet1['B3'] = repo_json.get('branch_name')

    sheet1['A4'] = 'Repository Name'
    sheet1['B4'] = repo_json.get('repo_name')

    sheet1['A5'] = 'Repo Is Private'
    sheet1['B5'] = repo_json.get('branch_is_protected')

    sheet1['A7'] = 'Branch Protections'

    sheet1['A8'] = 'Number Of Required Reviewers'
    sheet1['B8'] = repo_json.get('required_reviews')

    sheet1['A9'] = 'Reset On Source Push'
    sheet1['B9'] = repo_json.get('reset_on_source_push')

    sheet1['A10'] = 'Code Owners Required'
    sheet1['B10'] = repo_json.get('require_code_owner_reviews')

    sheet1['A11'] = 'Force Pushes Allowed'
    sheet1['B11'] = repo_json.get('allow_force_pushes')

    sheet1['A12'] = 'Deletions Allowed'
    sheet1['B12'] = repo_json.get('allow_deletion')

    sheet1['A15'] = 'User/Teams to dismiss reviews'
    sheet1['B15'] = 'N/A'
    sheet1['A16'] = 'User/Teams to bypass pull requests'
    sheet1['B16'] = 'N/A'

    sheet1['A17'] = 'Notes'
    sheet1['B17'] = '0'

    sheet2 = workbook.create_sheet(title="PR Log")
    pr_logs_json = json.loads(pr_logs)
    sheet2['A1'] = 'Pull Request ID'
    sheet2['B1'] = 'Pull Request URL'
    sheet2['C1'] = 'Pull Request Description'
    sheet2['D1'] = 'Pull Request Created At'
    sheet2['E1'] = 'Author Name'
    sheet2['F1'] = 'Author Profile'
    sheet2['G1'] = 'Incoming (Head) Repository'
    sheet2['H1'] = 'Incoming Ref Name'
    sheet2['I1'] = 'Incoming Commit'
    sheet2['J1'] = 'Merged By'
    sheet2['K1'] = 'Merged By Profile'
    sheet2['L1'] = 'Merged At'
    sheet2['M1'] = 'Target (Base) Repository'
    sheet2['N1'] = 'Target Ref Name'
    sheet2['O1'] = 'Target Commit'
    sheet2['P1'] = 'Latest Reviews'
    sheet2['Q1'] = 'Latest Comments'

    for i, (pr_json) in enumerate(pr_logs_json, start=2):  # Start at row 2
        sheet2[f'A{i}'] = pr_json.get('number')
        sheet2[f'B{i}'] = pr_json.get('url')
        sheet2[f'C{i}'] = pr_json.get('body')
        sheet2[f'D{i}'] = pr_json.get('created_at')
        sheet2[f'E{i}'] = pr_json.get('author')
        sheet2[f'F{i}'] = pr_json.get('author_url')
        sheet2[f'G{i}'] = pr_json.get('head_repo')
        sheet2[f'H{i}'] = pr_json.get('head_ref')
        sheet2[f'I{i}'] = pr_json.get('title')
        sheet2[f'J{i}'] = pr_json.get('merge_by')
        sheet2[f'K{i}'] = pr_json.get('merge_user_url')
        sheet2[f'L{i}'] = pr_json.get('merged_at')
        sheet2[f'M{i}'] = pr_json.get('target_ref')
        sheet2[f'N{i}'] = pr_json.get('target_repo')
        sheet2[f'O{i}'] = pr_json.get('commit_message')
        sheet2[f'P{i}'] = pr_json.get('latest_reviews')
        sheet2[f'Q{i}'] = pr_json.get('latest_comments')

    path = FILE_SAVE_BASE_PATH + repo_json.get('repo_name') + '_' + repo_json.get('branch_name') + '.xlsx'
    workbook.save(path)
    lookup_and_update_evidence(config.get("workspace_id"), config.get("evidence_id"), get_drata_api_key(), path)


def lookup_and_update_evidence(workspace_id, evidence_id, api_key, file_path):
    response = get_workspace_details(evidence_id, workspace_id, api_key)

    if response.status_code == 200:
        data = response.json()
        evidence_name = data.get('name')
        filed_at = data.get('filedAt')
        renewal_date = data.get('renewalDate')
        description = data.get('description')

        # 2. Update the evidence (using the provided file_path)
        url = f"{API_BASE_URL}/public/workspaces/{workspace_id}/evidence-library/{evidence_id}"
        mime_type, _ = mimetypes.guess_type(file_path)
        with open(file_path, 'rb') as file:
            files = {
                'file': (file_path.split('/')[-1], file, mime_type or 'application/octet-stream'),
                'name': (None, f"[{evidence_name}"),
                'description': (None, description),
                'renewalDate': (None, renewal_date.isoformat()),
                'filedAt': (None, filed_at.isoformat()),
                'ownerId': (None, str(OWNER_ID)),
                'source': (None, SOURCE_TYPE),
                'renewalScheduleType': (None, RENEWAL_SCHEDULE_TYPE)
            }
            headers = {
                'Authorization': f'Bearer {api_key}'
            }
            response = requests.put(url, files=files, headers=headers, timeout=10)

            if response.status_code == 200 or response.status_code == 204:
                print("Evidence updated successfully!")
            else:
                print(f"Error updating evidence: {response.status_code} - {response.text}")

    else:
        print(f"Error fetching evidence details: {response.status_code} - {response.text}")


def get_workspace_details(evidence_id, workspace_id, drata_api_key):
    url = f"{API_BASE_URL}/public/workspaces/{workspace_id}/evidence-library/{evidence_id}"

    headers = {'Authorization': f'Bearer {drata_api_key}'}

    response = requests.get(url, headers=headers, timeout=10)
    print(f'get_workspace_details response status code:{response.status_code} response message:{response.text}\n')
    return response


def get_drata_api_key():
    secret_name = "dev/darata/GitMergeReport"
    region_name = "us-east-2"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        print(f'{e.response}\n')
        raise e

    return json.loads(get_secret_value_response['SecretString'])['drata_api_key']


def get_private_key():
    secret_name = "test123"
    region_name = "us-east-2"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        print(f'{e.response}\n')
        raise e
    return get_secret_value_response['SecretString']


def get_token_git_app():
    private_key = get_private_key()

    app_id = '974338'

    payload = {
        'iat': int(datetime.now().timestamp() - 60),
        'exp': int(time.time()) + (4 * 60),
        'iss': app_id
    }

    jwt_token = jwt.encode(payload, private_key, algorithm='RS256')

    installation_id = '53960075'

    headers = {
        'Authorization': f'Bearer {jwt_token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    response = requests.post(
        f'{GIT_API_BASE_URL}/app/installations/{installation_id}/access_tokens',
        headers=headers
    )
    return response.json()['token']


# token = get_token_git_app()
# config_json = [{"owner": "malinda-peiris", "project": "facemymelody", "branch": "main"}]
# for config in config_json:
#     repo_details = get_branch_config_details(token, config)
#     pr_logs = fetch_pull_requests(token, config)
#     create_xlsx_update_evidence(repo_details, pr_logs, config)
