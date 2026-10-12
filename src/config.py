from ruamel.yaml import YAML

with open('config/config.yaml') as stream:
	config = YAML(typ='safe', pure=True).load(stream)
