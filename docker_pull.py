import os
import sys
import gzip
from io import BytesIO
import json
import hashlib
import shutil
import requests
import tarfile
import urllib3
import re
urllib3.disable_warnings()

# Graphical needs for drawing full line
try:
	console_rows, console_columns = os.popen('stty size', 'r').read().split()
except:
	console_rows, console_columns = 20 , 20

############# DEFAULTs VAR

DOCKER_DEFAULT_auth_url='auth.docker.io/token'
DOCKER_DEFAULT_server_url='registry-1.docker.io'
DOCKER_DEFAULT_repo = 'library'
DOCKER_DEFAULT_tag = 'latest'

username = ""
password = ""
output_path = "."

json_manifest_type='application/vnd.docker.distribution.manifest.v2+json'
json_manifest_type_bis='application/vnd.docker.distribution.manifest.list.v2+json'

############################################ FUNCTION ######################################################

# Get endpoint registry from url
def get_endpoint_registry(url,repository):
	resp = requests.get('https://{}/v2/'.format(url), verify=False)
	server_auth_url=""

    # If we get 401, we need to authenticate, so get server_auth_url
	if resp.status_code == 401:
		try:
			realm_address = re.search('realm="([^"]*)"',resp.headers['WWW-Authenticate'])

			# If Repository is on NEXUS OSS
			if realm_address.group(1) == "Sonatype Nexus Repository Manager":
				server_auth_url = "https://" + url + "/v2/"
				print ("Nexus OSS repository type")

			# If Repository is on DockerHub like
			if realm_address.group(1) != url and "http" in realm_address.group(1) :
				service = re.search('service="([^"]*)"',resp.headers['WWW-Authenticate'])
				server_auth_url = realm_address.group(1) + "?service=" + service.group(1) + "&scope=repository:" + repository + ":pull"
				print ("Docker Hub repository type")
			
		except IndexError:
			server_auth_url = "https://" + url + "/v2/"
			print ("failed !")
		
	return server_auth_url

# Get authentication headers
def get_auth_head(registry_endpoint,type):

	# Get authentication header from endpoint
	if len(username) != 0 and len(password) != 0:
		resp = requests.get('{}'.format(registry_endpoint), auth=(username, password),verify=False)
	else:
		resp = requests.get('{}'.format(registry_endpoint), verify=False)
	
	# Generate authentication header from response
	if (resp.status_code == 200):
		try:
			access_token = resp.json()['token']
			auth_head = {'Authorization':'Bearer '+ access_token, 'Accept': type}
		except ValueError:
			access_token = resp.request.headers['Authorization'].split("Basic ")[1]
			auth_head = {'Authorization':'Basic '+ access_token, 'Accept': type}
	elif (resp.status_code == 401):
		print ("Authentication error !")
		exit(1)
	else:
		print ("Erreur inside get_auth_head function : " + resp.status_code)

	return auth_head

# Docker style progress bar
def progress_bar(ublob, nb_traits):
	sys.stdout.write('\r' + ublob[7:19] + ': Downloading [')
	for i in range(0, nb_traits):
		if i == nb_traits - 1:
			sys.stdout.write('>')
		else:
			sys.stdout.write('=')
	for i in range(0, 49 - nb_traits):
		sys.stdout.write(' ')
	sys.stdout.write(']')
	sys.stdout.flush()

#/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\#

############################################## MAIN ########################################################

############## Check if args < 2

if len(sys.argv) < 2 :
	print ('Usage:')
	print ('\t docker_pull.py [registry/][repository/]image[:tag|@digest] ')
	print ('\t docker_pull.py [registry/][repository/]image[:tag|@digest] output_path')
	print ('\t docker_pull.py [registry/][repository/]image[:tag|@digest] username password output_path\n')
	exit(1)

############## Get info from arg 

imgparts = sys.argv[1].split('/')

############## Setup username & password
if len(sys.argv) == 3:
	output_path = sys.argv[2]

if len(sys.argv) == 4:
    username = sys.argv[2] 
    password = sys.argv[3] 

if len(sys.argv) == 5:
	username = sys.argv[2] 
	password = sys.argv[3] 
	output_path = sys.argv[4]

############## Get repository url + registry url for auth

if len(imgparts) > 1 and ('.' in imgparts[0] or ':' in imgparts[0]):
	registry_url = imgparts[0]
	repository = imgparts[1]

	if len(imgparts[:-2]) != 0:
		img = ('/'.join(imgparts[2:])).split(':')[0]
		tag = ('/'.join(imgparts[2:])).split(':')[1]
	else:
		img = (imgparts[2:]).split(':')[0]
		tag = (imgparts[2:]).split(':')[1]
else:
	registry_url = DOCKER_DEFAULT_server_url

	if len(imgparts[:-1]) != 0:
		img = "" # FIXME: Image name on docker hub is actually the repository url
		tag = ('/'.join(imgparts).split('/')[1].split(':')[1])
		repository = ('/'.join(imgparts).split(':')[0])
	else:
		img = "" # FIXME: Image name on docker hub is actually the repository url
		tag = (imgparts[0]).split(':')[1]
		repository = "library/" + (imgparts[0]).split(':')[0]

############## Get Registry Authentication endpoint when it is required
registry_endpoint = get_endpoint_registry(registry_url,repository)

# Printing vars

print('_'*int(console_columns))
print ("\nDocker image :\t\t\t" + img)
print ("Docker tag :\t\t\t" + tag)
print ("Repository :\t\t\t" + repository )
print ("Serveur_URL :\t\t\t" + "https://" + registry_url )
print ( "Registry_endpoint :\t\t" + registry_endpoint)
print('_'*int(console_columns))

############## Fetch manifest v2 and get image layer digests

# Get manifest v2
auth_head=get_auth_head(registry_endpoint,json_manifest_type)

resp = requests.get('https://{}/v2/{}/{}/manifests/{}'.format(registry_url, repository, img, tag), headers=auth_head, verify=False)

# Check if error (not getting manifest)
if (resp.status_code != 200):
	print('[-] Cannot fetch manifest for {} [HTTP {}]'.format(sys.argv[1], resp.status_code))

	# Retry with other json_manifest_type
	auth_head = get_auth_head(registry_endpoint,json_manifest_type_bis)
	resp = requests.get('https://{}/v2/{}/{}/manifests/{}'.format(registry_url, repository, img, tag), headers=auth_head, verify=False)

	if (resp.status_code == 200):
		print('[+] Manifests found for this tag (use the @digest format to pull the corresponding image):')
		manifests = resp.json()['manifests']
		for manifest in manifests:
			for key, value in manifest["platform"].items():
				sys.stdout.write('{}: {}, '.format(key, value))
			print('digest: {}'.format(manifest["digest"]))
	elif (resp.status_code == 401):
		print ("Authentication needed !")
		exit(1)
	else:
		print("Error when getting manifest response status code : " + str(resp.status_code))
		exit(1)

# Get all layers from manifest    
layers = resp.json()['layers']

# Create tmp folder that will hold the image
imgdir = output_path  + '/tmp_{}'.format(sys.argv[1].replace('/', '.').replace(':','@'))

if os.path.exists(imgdir):
    shutil.rmtree(imgdir)

os.mkdir(imgdir)
print('Creating image structure in: ' + imgdir)

# Get SHA256 ID image
config = resp.json()['config']['digest']

# Get manifest for SHA256 ID image
confresp = requests.get('https://{}/v2/{}/blobs/{}'.format(registry_url, repository, config), headers=auth_head, verify=False)

# Write manifest inside file
file = open('{}/{}.json'.format(imgdir, config[7:]), 'wb')
file.write(confresp.content)
file.close()

# Prepare content args for json
content = [{
	'Config': config[7:] + '.json',
	'RepoTags': [ ],
	'Layers': [ ]
	}]

# Set content tag
content[0]['RepoTags'].append(sys.argv[1])

# Prepare template json
empty_json = '{"created":"1970-01-01T00:00:00Z","container_config":{"Hostname":"","Domainname":"","User":"","AttachStdin":false, \
	"AttachStdout":false,"AttachStderr":false,"Tty":false,"OpenStdin":false, "StdinOnce":false,"Env":null,"Cmd":null,"Image":"", \
	"Volumes":null,"WorkingDir":"","Entrypoint":null,"OnBuild":null,"Labels":null}}'

# Build layer folders
parentid=''
for layer in layers:

    #Get digest of layer
	ublob = layer['digest']

	# FIXME: Creating fake layer ID. Don't know how Docker generates it
	fake_layerid = hashlib.sha256((parentid+'\n'+ublob+'\n').encode('utf-8')).hexdigest()
	layerdir = imgdir + '/' + fake_layerid
	os.mkdir(layerdir)

	# Creating VERSION file
	file = open(layerdir + '/VERSION', 'w')
	file.write('1.0')
	file.close()

	# Creating layer.tar file
	sys.stdout.write(ublob[7:19] + ': Downloading...')
	sys.stdout.flush()
	auth_head = get_auth_head(registry_endpoint,json_manifest_type) # refreshing token to avoid its expiration

	bresp = requests.get('https://{}/v2/{}/blobs/{}'.format(registry_url, repository, ublob), headers=auth_head, stream=True, verify=False)
	if (bresp.status_code != 200): # When the layer is located at a custom URL
		bresp = requests.get(layer['urls'][0], headers=auth_head, stream=True, verify=False)
		if (bresp.status_code != 200):
			print('\rERROR: Cannot download layer {} [HTTP {}]'.format(ublob[7:19], bresp.status_code, bresp.headers['Content-Length']))
			print(bresp.content)
			exit(1)

	# Stream download and follow the progress
	bresp.raise_for_status()
	unit = int(bresp.headers['Content-Length']) / 50
	acc = 0
	nb_traits = 0
	progress_bar(ublob, nb_traits)
	with open(layerdir + '/layer_gzip.tar', "wb") as file:
		for chunk in bresp.iter_content(chunk_size=8192): 
			if chunk:
				file.write(chunk)
				acc = acc + 8192
				if acc > unit:
					nb_traits = nb_traits + 1
					progress_bar(ublob, nb_traits)
					acc = 0
	sys.stdout.write("\r{}: Extracting...{}".format(ublob[7:19], " "*50)) # Ugly but works everywhere
	sys.stdout.flush()
	with open(layerdir + '/layer.tar', "wb") as file: # Decompress gzip response
		unzLayer = gzip.open(layerdir + '/layer_gzip.tar','rb')
		shutil.copyfileobj(unzLayer, file)
		unzLayer.close()
	os.remove(layerdir + '/layer_gzip.tar')
	print("\r{}: Pull complete [{}]".format(ublob[7:19], bresp.headers['Content-Length']))
	content[0]['Layers'].append(fake_layerid + '/layer.tar')
	
	# Creating json file
	file = open(layerdir + '/json', 'w')
	# last layer = config manifest - history - rootfs
	if layers[-1]['digest'] == layer['digest']:
		# FIXME: json.loads() automatically converts to unicode, thus decoding values whereas Docker doesn't
		json_obj = json.loads(confresp.content)
		del json_obj['history']
		try:
			del json_obj['rootfs']
		except: # Because Microsoft loves case insensitiveness
			del json_obj['rootfS']
	else: # other layers json are empty
		json_obj = json.loads(empty_json)
	json_obj['id'] = fake_layerid
	if parentid:
		json_obj['parent'] = parentid
	parentid = json_obj['id']
	file.write(json.dumps(json_obj))
	file.close()

file = open(imgdir + '/manifest.json', 'w')
file.write(json.dumps(content))
file.close()

if len(imgparts[:-1]) != 0:
	content = { '/'.join(imgparts[:-1]) + '/' + img : { tag : fake_layerid } }
else: # when pulling only an img (without repo and registry)
	content = { img : { tag : fake_layerid } }
file = open(imgdir + '/repositories', 'w')
file.write(json.dumps(content))
file.close()

# Create image tar and clean tmp folder
docker_tar = output_path  + "/" + sys.argv[1].replace('/', '_').replace(':','@') + '.tar'
sys.stdout.write("Creating archive...")
sys.stdout.flush()
tar = tarfile.open(docker_tar, "w")
tar.add(imgdir, arcname=os.path.sep)
tar.close()
shutil.rmtree(imgdir)
print('\rDocker image pulled: ' + docker_tar)