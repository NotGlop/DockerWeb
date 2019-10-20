from __future__ import print_function

import os
import sys
import json
import hashlib
import shutil
import requests
import tarfile

from config import verify, auth

try:
    FileNotFoundError
except NameError:
    # Create FileNotFoundError if using python 2.7
    FileNotFoundError = OSError

if len(sys.argv) != 2:
    # TODO: Use argparse instead
    raise ValueError('Usage:\n\tdocker_pull.py [[registry/]repository/]image[:tag]\n')

# Request parameters
params = {
    'verify': verify,
    'auth': auth,
    'allow_redirects': True
}

# Default docker info
DEFAULT_URL = 'registry-1.docker.io'
DEFAULT_REPO = 'library'
DEFAULT_TAG = 'latest'

# Get url, name, image, tag
user_string = sys.argv[1]
imgtag = user_string.split('/')[-1]
try:
    img, tag = imgtag.split(':')
except ValueError:
    img = imgtag
    tag = DEFAULT_TAG

try:
    url, repo, _ = user_string.split('/')
except ValueError:
    url = DEFAULT_URL
    try:
        repo, _ = user_string.split('/')
    except ValueError:
        repo = DEFAULT_REPO
base_url = 'https://{url}/v2'.format(url=url)
name = '{repo}/{img}'.format(repo=repo, img=img)

# Create tmp folder that will hold the image
temp_dir = 'tmp_{img}_{tag}'.format(img=img, tag=tag)
try:
    shutil.rmtree(temp_dir + 'abc')  # try to delete temp dir if it exists
except FileNotFoundError:
    pass
os.mkdir(temp_dir)
print('Creating image structure in: ' + temp_dir)

# Get Docker token
resp = requests.get(
    'https://auth.docker.io/token?service=registry.docker.io&scope=repository:{name}:pull'.format(name=name),
    **params
)
access_token = resp.json()['access_token']
auth_head = {
    'Authorization': 'Bearer {access_token}'.format(access_token=access_token),
    'Accept': 'application/vnd.docker.distribution.manifest.v2+json'
}

# Get image manifest
resp = requests.get(
    '{base_url}/{name}/manifests/{tag}'.format(base_url=base_url, name=name, tag=tag),
    headers=auth_head,
    **params
)
if not resp.ok:
    raise requests.exceptions.ConnectionError(
        'Cannot fetch manifest for {name}:{tag} [HTTP {code}]'.format(name=name, tag=tag, code=resp.status_code)
    )

# Get layers and digest from manifest
layers = resp.json()['layers']
digest = resp.json()['config']['digest']
# digest_algorithm = digest.split(':')[0]
digest_hash = digest.split(':')[1]

# Get config layer
resp = requests.get(
    '{base_url}/{name}/blobs/{digest}'.format(base_url=base_url, name=name, digest=digest),
    headers=auth_head,
    **params
)
config_json = resp.content

# Save config JSON
with open(temp_dir + '/' + digest_hash + '.json', 'wb') as fo:
    fo.write(config_json)

tar_manifest = [
    {
        'Config': digest_hash + '.json',
        'RepoTags': [name + ':' + tag],
        'Layers': []
    }
]

empty_json = json.dumps(
    {
        'created': '1970-01-01T00:00:00Z',
        'container_config': {
            'Hostname': '',
            'Domainname': '',
            'User': '',
            'AttachStdin': False,
            'AttachStdout': False,
            'AttachStderr': False,
            'Tty': False,
            'OpenStdin': False,
            'StdinOnce': False,
            'Env': None,
            'Cmd': None,
            'Image': '',
            'Volumes': None,
            'WorkingDir': '',
            'Entrypoint': None,
            'OnBuild': None,
            'Labels': None
        }
    }
)

# Build layer folders
parent_id = ''
CHUNK_SIZE = 5120  # chunk size for tar file download (see requests.iter_content docs for details)
for layer in layers:
    # Get digest info
    digest = layer['digest']
    digest_hash = digest.split(':')[1]

    # Create fake layer id
    # FIXME: Creating fake layer ID. Don't know how Docker generates it
    hash_content = '{parent_id}\n{digest}\n'.format(parent_id=parent_id, digest=digest).encode('utf-8')
    fake_layer_id = hashlib.sha256(hash_content).hexdigest()
    tar_manifest[0]['Layers'].append(fake_layer_id + '/layer.tar')
    layer_dir = temp_dir + '/' + fake_layer_id
    os.mkdir(layer_dir)

    # Get url for layer.tar file
    try:
        tar_url = layer['urls'][0]  # API may provide redirect
    except KeyError:
        tar_url = '{base_url}/{name}/blobs/{digest}'.format(base_url=base_url, name=name, digest=digest)

    # Download layer.tar file
    print('Downloading ' + digest_hash[:10])
    s = requests.session()
    resp = s.get(tar_url, headers=auth_head, stream=True, **params)
    resp.raise_for_status()
    with open(layer_dir + '/layer.tar', 'wb') as fo:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                fo.write(chunk)
    print('Download complete: ' + resp.headers['Content-Length'] + ' bytes')

    # Create VERSION file
    with open(layer_dir + '/VERSION', 'w') as fo:
        fo.write('1.0')

    # Create JSON file
    with open(layer_dir + '/json', 'w') as fo:
        # last layer = config manifest - history - rootfs
        if layers[-1]['digest'] == digest:
            # FIXME: json.loads() automatically converts to unicode, thus decoding values whereas Docker doesn't
            if isinstance(config_json, str):
                json_obj = json.loads(config_json)
            else:
                # added for Python 3.5 compatibility since config_json is a bytes object rather than a string
                json_obj = json.loads(config_json.decode())
            del json_obj['history']
            del json_obj['rootfs']
        else:  # other layers json are empty
            json_obj = json.loads(empty_json)

        json_obj['id'] = fake_layer_id
        if parent_id:
            json_obj['parent'] = parent_id
        fo.write(json.dumps(json_obj))

    # Set next layer's parent id
    parent_id = fake_layer_id

# Create tar manifest file
with open(temp_dir + '/manifest.json', 'w') as fo:
    fo.write(json.dumps(tar_manifest))

# Create tar repositories file
tar_repositories = {
    name: {
        tag: fake_layer_id
    }
}
with open(temp_dir + '/repositories', 'w') as fo:
    fo.write(json.dumps(tar_repositories))

# Create image tar
docker_tar = '{repo}_{img}.tar'.format(repo=repo, img=img)
with tarfile.open(docker_tar, "w") as tar:
    tar.add(temp_dir, arcname=os.path.sep)
print('Docker image pulled: ' + docker_tar)

# Clean up temp dir
shutil.rmtree(temp_dir)
print('Removed temp dir: ' + temp_dir)
print('Done')
