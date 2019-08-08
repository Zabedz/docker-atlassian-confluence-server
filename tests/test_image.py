import pytest

import io
import tarfile
import testinfra
import time
from lxml import etree

import requests


# Run an image and wrap it in a TestInfra host for convenience.
# FIXME: There's probably a way to turn this into a fixture with parameters.
def run_image(docker_cli, image, environment={}, ports={}):
    container = docker_cli.containers.run(image, environment=environment, ports=ports, detach=True)
    return testinfra.get_host("docker://"+container.id)

# TestInfra's process command doesn't seem to work for arg matching
def get_procs(container):
    ps = container.run('ps -axo args')
    return ps.stdout.split('\n')

def wait_for_proc(container, proc_str, max_wait=10):
    waited = 0
    while waited < max_wait:
        procs = list(filter(lambda p: proc_str in p, get_procs(container)))
        if len(procs) > 0:
            return procs[0]
        time.sleep(0.1)
        waited += 0.1

    raise TimeoutError("Failed to find target process")


######################################################################
# Tests

def test_jvm_args(docker_cli, image):
    environment = {
        'JVM_MINIMUM_MEMORY': '383m',
        'JVM_MAXIMUM_MEMORY': '2047m',
        'JVM_SUPPORT_RECOMMENDED_ARGS': '-verbose:gc',
    }
    container = run_image(docker_cli, image, environment=environment)
    jvm = wait_for_proc(container, "org.apache.catalina.startup.Bootstrap")

    assert f'-Xms{environment.get("JVM_MINIMUM_MEMORY")}' in jvm
    assert f'-Xmx{environment.get("JVM_MAXIMUM_MEMORY")}' in jvm
    assert environment.get('JVM_SUPPORT_RECOMMENDED_ARGS') in jvm


def test_install_permissions(docker_cli, image):
    container = run_image(docker_cli, image)

    assert container.file('/opt/atlassian/confluence/conf/server.xml').user == 'root'

    for d in ['logs', 'work', 'temp']:
        path = '/opt/atlassian/confluence/{}/'.format(d)
        assert container.file(path).user == 'confluence'


def test_first_run_state(docker_cli, image):
    PORT = 8090
    container = run_image(docker_cli, image, ports={PORT: PORT})
    jvm = wait_for_proc(container, "org.apache.catalina.startup.Bootstrap")

    for i in range(20):
        try:
            r = requests.get(f'http://localhost:{PORT}/status')
        except requests.exceptions.ConnectionError:
            pass
        else:
            if r.status_code == 200:
                state = r.json().get('state')
                assert state in ('STARTING', 'FIRST_RUN')
                return
        time.sleep(1)
    raise TimeoutError


def test_server_xml_defaults(docker_cli, image):
    container = run_image(docker_cli, image)
    _jvm = wait_for_proc(container, "org.apache.catalina.startup.Bootstrap")

    xml = etree.fromstring(container.file('/opt/atlassian/confluence/conf/server.xml').content)
    connector = xml.find('.//Connector')
    context = xml.find('.//Context')

    assert connector.get('port') == '8090'
    assert connector.get('maxThreads') == '200'
    assert connector.get('minSpareThreads') == '10'
    assert connector.get('connectionTimeout') == '20000'
    assert connector.get('enableLookups') == 'false'
    assert connector.get('protocol') == 'HTTP/1.1'
    assert connector.get('acceptCount') == '10'
    assert connector.get('secure') == 'false'
    assert connector.get('scheme') == 'http'
    assert connector.get('proxyName') == ''
    assert connector.get('proxyPort') == ''

def test_server_xml_params(docker_cli, image):
    environment = {
        'ATL_TOMCAT_MGMT_PORT': '8006',
        'ATL_TOMCAT_PORT': '9090',
        'ATL_TOMCAT_MAXTHREADS': '201',
        'ATL_TOMCAT_MINSPARETHREADS': '11',
        'ATL_TOMCAT_CONNECTIONTIMEOUT': '20001',
        'ATL_TOMCAT_ENABLELOOKUPS': 'true',
        'ATL_TOMCAT_PROTOCOL': 'HTTP/2',
        'ATL_TOMCAT_ACCEPTCOUNT': '11',
        'ATL_TOMCAT_SECURE': 'true',
        'ATL_TOMCAT_SCHEME': 'https',
        'ATL_PROXY_NAME': 'jira.atlassian.com',
        'ATL_PROXY_PORT': '443',
        'ATL_TOMCAT_CONTEXTPATH': '/myjira',
    }
    container = run_image(docker_cli, image, environment=environment)
    _jvm = wait_for_proc(container, "org.apache.catalina.startup.Bootstrap")

    xml = etree.fromstring(container.file('/opt/atlassian/confluence/conf/server.xml').content)
    connector = xml.find('.//Connector')
    context = xml.find('.//Context')

    assert xml.get('port') == environment.get('ATL_TOMCAT_MGMT_PORT')

    assert connector.get('port') == environment.get('ATL_TOMCAT_PORT')
    assert connector.get('maxThreads') == environment.get('ATL_TOMCAT_MAXTHREADS')
    assert connector.get('minSpareThreads') == environment.get('ATL_TOMCAT_MINSPARETHREADS')
    assert connector.get('connectionTimeout') == environment.get('ATL_TOMCAT_CONNECTIONTIMEOUT')
    assert connector.get('enableLookups') == environment.get('ATL_TOMCAT_ENABLELOOKUPS')
    assert connector.get('protocol') == environment.get('ATL_TOMCAT_PROTOCOL')
    assert connector.get('acceptCount') == environment.get('ATL_TOMCAT_ACCEPTCOUNT')
    assert connector.get('secure') == environment.get('ATL_TOMCAT_SECURE')
    assert connector.get('scheme') == environment.get('ATL_TOMCAT_SCHEME')
    assert connector.get('proxyName') == environment.get('ATL_PROXY_NAME')
    assert connector.get('proxyPort') == environment.get('ATL_PROXY_PORT')

    assert context.get('path') == environment.get('ATL_TOMCAT_CONTEXTPATH')


def test_seraph_defaults(docker_cli, image):
    container = run_image(docker_cli, image)
    _jvm = wait_for_proc(container, "org.apache.catalina.startup.Bootstrap")

    xml = etree.fromstring(container.file('/opt/atlassian/confluence/confluence/WEB-INF/classes/seraph-config.xml').content)
    param = xml.xpath('//param-name[text()="autologin.cookie.age"]')[0].getnext()
    assert param.text == "1209600"


def test_seraph_login_set(docker_cli, image):
    container = run_image(docker_cli, image, environment={"ATL_AUTOLOGIN_COOKIE_AGE": "TEST_VAL"})
    _jvm = wait_for_proc(container, "org.apache.catalina.startup.Bootstrap")

    xml = etree.fromstring(container.file('/opt/atlassian/confluence/confluence/WEB-INF/classes/seraph-config.xml').content)
    param = xml.xpath('//param-name[text()="autologin.cookie.age"]')[0].getnext()
    assert param.text == "TEST_VAL"

#
# def test_confluence_cfg_xml_defaults(docker_cli, image):
#     environment = {
#
#     }
#     container = docker_cli.containers.run(image, environment=environment, detach=True)
#     confluence_cfg_xml = get_fileobj_from_container(container, '/var/atlassian/application-data/confluence/confluence.cfg.xml')
#     xml = etree.parse(confluence_cfg_xml)
#
#
# def test_confluence_cfg_xml_params(docker_cli, image):
#     environment = {
#
#     }
#     container = docker_cli.containers.run(image, environment=environment, detach=True)
#     confluence_cfg_xml = get_fileobj_from_container(container, '/var/atlassian/application-data/confluence/confluence.cfg.xml')
#     xml = etree.parse(confluence_cfg_xml)
