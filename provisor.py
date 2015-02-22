import copy
import ldap
import ldap.modlist
import time
import crypt
import os
import re

LDAP_URI="ldap://ldap.hashbang.sh"
LDAP_USER="cn=provisor,ou=Admin,dc=hashbang,dc=sh"
USER_BASE="ou=People,dc=hashbang,dc=sh"
GROUP_BASE="ou=Group,dc=hashbang,dc=sh"

DEFAULT_SHELL = "/bin/bash"
MIN_UID = 3000
MAX_UID = 1000000
EXCLUDED_UIDS = (65534,)

def make_salt():
  salt = ""
  while len(salt) < 8:
    c = os.urandom(1)
    if re.match('[a-zA-Z0-9./]', c):
      salt += c
  return salt



class Provisor(object):
  def __init__(self, passwd):
    self.con = ldap.initialize(LDAP_URI)
    self.con.set_option(ldap.OPT_X_TLS_DEMAND, True)
    self.con.start_tls_s()
    self.con.simple_bind_s(LDAP_USER, passwd)

  """ Does not work, dont know why """
  def whoami(self):
    return self.con.whoami_s()

  def list_users(self):
    users = []
    results = self.con.search_s(USER_BASE, ldap.SCOPE_ONELEVEL, '(objectClass=*)', ("uid",), 0)
    for r in results:
      for attrs in r[1]:
        users.append(r[1][attrs][0])
    return tuple(users)

  def list_groups(self):
    groups = []
    results = self.con.search_s(GROUP_BASE, ldap.SCOPE_ONELEVEL, '(objectClass=*)', ("cn",), 0)
    for r in results:
      for attrs in r[1]:
        groups.append(r[1][attrs][0])
    return tuple(groups)

  def group_exists(self, group):
    try:
      if self.con.compare_s("cn={0},{1}".format(group, GROUP_BASE), "cn", group) == 1:
        return True
      else:
        return False
    except ldap.NO_SUCH_OBJECT:
      return False

  def user_exists(self, user):
    try:
      if self.con.compare_s("uid={0},{1}".format(user, USER_BASE), "uid", user) == 1:
        return True
      else:
        return False
    except ldap.NO_SUCH_OBJECT:
      return False

  """ Returns the next uid for use """
  def next_uid(self):
    uids = []
    results = self.con.search_s(USER_BASE, ldap.SCOPE_ONELEVEL, '(objectClass=*)', ("uidNumber",), 0)
    for r in results:
      for attrs in r[1]:
        uids.append(int(r[1][attrs][0]))
    uids.sort()
    for u in range(MIN_UID,MAX_UID,1):
      if u in uids or u in EXCLUDED_UIDS:
        continue
      return u

  """ Returns the next gid for use """
  def next_gid(self):
    gids = []
    results = self.con.search_s(GROUP_BASE, ldap.SCOPE_ONELEVEL, '(objectClass=*)', ("gidNumber",), 0)
    for r in results:
      for attrs in r[1]:
        gids.append(int(r[1][attrs][0]))
    gids.sort()
    for g in range(MIN_UID,MAX_UID,1):
      if g in gids or g in EXCLUDED_UIDS:
        continue
      return g


  def add_group(self, groupname, gid=-1):
    if gid < 0:
      self.next_gid()

    ml = {
     'objectClass': [ 'top','posixGroup'],
     'cn': [ groupname ],
     'gidNumber': [ str(gid) ],
    }
    ml = ldap.modlist.addModlist(ml)
    self.con.add_s("cn={0},{1}".format(groupname, GROUP_BASE), ml)


  def del_group(self, groupname):
    self.con.delete_s("cn={0},{1}".format(groupname, GROUP_BASE))



  def is_group_member(self, group, user):
    try:
      if self.con.compare_s("cn={0},{1}".format(group, GROUP_BASE), "memberUid", user) == 1:
        return True
      else:
        return False
    except ldap.NO_SUCH_OBJECT:
      return False


  def list_group_members(self, group):
    members = []
    results = self.con.search_s("cn={0},{1}".format(group,GROUP_BASE), 
                                      ldap.SCOPE_BASE, '(objectClass=*)', ("memberUid",), 0)
    for r in results:
      for attrs in r[1]:
        for e in r[1][attrs]:
          members.append(e)
    return members


  def add_group_member(self, group, user):
    ml = { 'memberUid': [ user ] }
    ml = ldap.modlist.modifyModlist({}, ml, ignore_oldexistent=1)
    self.con.modify_s("cn={0},{1}".format(group, GROUP_BASE), ml)


  def del_group_member(self, group, user):
    old = self.con.search_s("cn={0},{1}".format(group, GROUP_BASE), ldap.SCOPE_BASE, '(objectClass=*)', ("memberUid",), 0)
    old = old[0][1]
    new = copy.deepcopy(old)
    new['memberUid'].remove(user)
    ml = ldap.modlist.modifyModlist(old, new)
    self.con.modify_s("cn={0},{1}".format(group, GROUP_BASE), ml)



  """ Attempt to modify a users entry """
  def modify_user(self, username, pubkey=None,
                  shell=None, homedir=None, password=None,
                  uid=None, gid=None, lastchange=None, 
                  nextchange=None, warning=None, raw_passwd=None,
                  hostname=None):
    old = self.con.search_s("uid={0},{1}".format(username, USER_BASE), ldap.SCOPE_BASE, '(objectClass=*)', ("*",), 0)
    old = old[0][1]
    new = copy.deepcopy(old)

    if 'shadowAccount' not in new['objectClass']:
      new['objectClass'].append('shadowAccount')

    if 'inetLocalMailRecipient' not in new['objectClass']:
      new['objectClass'].append('inetLocalMailRecipient')

    if pubkey:
      if 'sshPublicKey' in new:
        del(new['sshPublicKey'])
      new['sshPublicKey'] = [ str(pubkey) ]

    if shell:
      if 'loginShell' in new:
        del(new['loginShell'])
      new['loginShell'] = [ str(shell) ]

    if homedir:
      if 'homeDirectory' in new:
        del(new['homeDirectory'])
      new['homeDirectory'] = [ str(homedir) ]

    if password:
      password = '{crypt}' + crypt.crypt(password, "$6${0}".format(make_salt()))
      if 'userPassword' in new:
        del(new['userPassword'])
      new['userPassword'] = [ str(password) ]

      if 'shadowLastChange' in new:
        del(new['shadowLastChange'])
      new['shadowLastChange'] = [ str(int(time.time() / 86400)) ]

    if raw_passwd:
      password = '{crypt}' + raw_passwd 
      if 'userPassword' in new:
        del(new['userPassword'])
      new['userPassword'] = [ str(password) ]

      if 'shadowLastChange' in new:
        del(new['shadowLastChange'])
      new['shadowLastChange'] = [ str(int(time.time() / 86400)) ]

      
    if lastchange:
      if 'shadowLastChange' in new:
        del(new['shadowLastChange'])
      new['shadowLastChange'] = [ str(int(time.time() / 86400)) ]

    if uid:
      if 'uidNumber' in new:
        del(new['uidNumber'])
      new['uidNumber'] = [ str(uid) ]

    if gid:
      if 'gidNumber' in new:
        del(new['gidNumber'])
      new['gidNumber'] = [ str(gid) ]

    if 'shadowInactive' not in new:
      new['shadowInactive'] = [ '99999' ]

    if 'shadowExpire' not in new:
      new['shadowExpire'] = [ '99999']

    if hostname:
      if 'host' in new:
        del(new['host'])
      new['host'] = str(hostname)
      if 'mailRoutingAddress' in new:
        del(new['mailRoutingAddress'])
      new['mailRoutingAddress'] = [ '{0}@hashbang.sh'.format(username) ]
      if 'mailHost' in new:
        del(new['mailHost'])
      new['mailHost'] = [ 'smtp:{0}'.format(hostname) ]

    ml = ldap.modlist.modifyModlist(old, new)
    self.con.modify_s("uid={0},{1}".format(username, USER_BASE), ml)



  """ Adds a user, takes a number of optional defaults but the username and public key are required """
  def add_user(self, username, pubkey, hostname,
                shell=DEFAULT_SHELL, homedir=None, password=None,
                uid=-1, gid=-1,
                lastchange=-1, nextchange=99999, warning=7, raw_passwd=None):

    if not homedir:
      homedir="/home/{0}".format(username)

    if uid < 0:
      uid = self.next_uid()
    if gid < 0:
      gid = self.next_gid()

    if lastchange < 0:
      lastchange = int(time.time() / 86400)

    if password == None:
      password = '{crypt}!'
    elif raw_passwd:
      password = '{crypt}' + raw_passwd
    else:
      password = '{crypt}' + crypt.crypt(password, "$6${0}".format(make_salt()))

    ml = {
      'objectClass': [ 'account', 'posixAccount', 'top' ,'shadowAccount', 'ldapPublicKey', 'inetLocalMailRecipient' ],
      'uid' : [ username ],
      'cn' : [ username],
      'uidNumber' : [ str(uid) ],
      'gidNumber' : [ str(gid) ],
      'loginShell' : [ DEFAULT_SHELL ],
      'homeDirectory' : [ homedir ],
      'shadowLastChange' : [ str(lastchange) ],
      'shadowMax' : [ str(nextchange) ],
      'shadowWarning' : [ str(warning) ],
      'shadowInactive' : [ str(99999) ],
      'shadowExpire' : [ str(99999) ],
      'userPassword' : [ str(password) ],
      'sshPublicKey' : [ str(pubkey) ],
      'host' : [ str(hostname) ],
      'mailRoutingAddress' : [ '{0}@hashbang.sh'.format(username) ],
      'mailHost' : [ str('smtp:'+hostname) ],
    }

    ml = ldap.modlist.addModlist(ml)
    self.con.add_s("uid={0},{1}".format(username, USER_BASE), ml)


  def del_user(self, username):
    self.con.delete_s("uid={0},{1}".format(username, USER_BASE))


  def __del__(self):
    self.con.unbind_s()



#c = Provisor("the_provisor_ldap_password")
#print c.whoami()
#print c.list_users()
#print c.list_groups()
#print c.user_exists("deleriux")
#print c.user_exists("nothing")
#print c.next_uid()
#c.add_group("somegroup", c.next_gid())
#c.del_group("somegroup")
#print c.is_group_member("sudo", "deleriux")
#print c.is_group_member("sudo", "bobby")
#print c.list_group_members("sudo")
#c.add_group_member("sudo", "deleriux")
#c.add_group_member("sudo", "jimmeh")
#c.add_group_member("sudo", "lrvick")
#c.del_group_member("sudo", "jimmeh")
#c.add_user("deleriux2", "abc123", password="someuserpassword")
#c.del_user("deleriux2")
#c.modify_user("deleriux", pubkey="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC2d7b7gu6v86gGp4zYzPX+ZhCbrPuFqSzRNXqWH+YZ9BOwlg5z1Dh61hSxqL/FhIsg3Y8w3Wc5/wUlBevW2hFmea2T4CkDetYAZuKHM2rUdCn7GufdSDPzhCkDPFPtILPp1wa18SC4DHGeFcPl3J8k/BDQOiIxnaY4e1mVk/2dHxUjTmXKMby6IfhsdCgSK5Vub+m/P1EKYT8pL2OeA1byzb0xKtLu7ALKTlnmgzEjo2bi5h4Pfreuu+0sQxlb9l65CH+oF4PHZKG13a8OVuz6nudMXBuNrqdLWAJgbvQ7QeZ0clcSsZEV7I8IYFKvhg+V/BihxCSnSaDYlYOWx0nHqrG2z7ERGnXRu1h11+TlmrtA3U0ws1XPCzO6F6Y0orEOzijW1lTQSBv3ec9T3YbK58zCYn9eDMHyAqXhpVx6nZkM01+f93UYBO5QwzMmIoX5KSt1ZfEDvpFjrZE7d3EZu+f4U/ETAN58NPvubL7Zqqsxn+5P11+opQDa7CnVH7bRs04MkIYjO7ofYMKDBx7VGmGjh0/3WNUBfNXahKIS+vq6yUYzZ/eJg7ONvG+4Q6exg6NFxV1GO9DB+RDp1aJcaMhaQ8z6oMhHixX2LMlbrRSWs8EypG+jgcfIZxImp8ODVC9D4t2Ec3AI10STaU9qNKnxQShtie108w+jD0J0Sw== matthew@home.localdomain")
#del(c)