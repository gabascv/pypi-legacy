
import sys, os, urllib, StringIO, traceback, cgi, binascii, getopt
import time, whrandom, smtplib, base64, sha

import store, config

class NotFound(Exception):
    pass
class Unauthorised(Exception):
    pass
class Redirect(Exception):
    pass
class FormError(Exception):
    pass

# email sent to user indicating how they should complete their registration
rego_message = '''Subject: Complete your registration
To: %(email)s

To complete your registration of the user "%(name)s" with the python module
index, please visit the following URL:

  %(url)s?:action=user&otk=%(otk)s

'''

# password reset email - indicates what the password is now
password_message = '''Subject: Password has been reset
To: %(email)s

Your login is: %(name)s
Your password is now: %(password)s
'''

chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'

class WebUI:
    ''' Handle a request as defined by the "env" parameter. "handler" gives
        access to the user via rfile and wfile, and a few convenience
        functions (see pypi.cgi).
        
        The handling of a request goes as follows:
        1. open the database
        2. see if the request is supplied with authentication information
        3. perform the action defined by :action ("index" if none is supplied)
        4a. handle exceptions sanely, including special ones like NotFound,
            Unauthorised, Redirect and FormError, or
        4b. commit changes to the database
        5. close the database to finish off

    '''
    def __init__(self, handler, env):
        self.handler = handler
        self.config = handler.config
        self.wfile = handler.wfile
        self.env = env
        self.form = cgi.FieldStorage(fp=handler.rfile, environ=env)
        whrandom.seed(int(time.time())%256)

    def run(self):
        ''' Run the request, handling all uncaught errors and finishing off
            cleanly.
        '''
        self.store = store.Store(self.config)
        self.store.open()
        try:
            try:
                self.inner_run()
            except NotFound:
                self.handler.send_error(404)
            except Unauthorised, message:
                message = str(message)
                if not message:
                    message = 'You must login to access this feature'
                self.fail(message, code=401, heading='Login required',
                    headers={'WWW-Authenticate':
                    'Basic realm="python packages index"'})
            except Redirect, path:
                self.handler.send_response(301)
                self.handler.send_header('Location', path)
            except FormError, message:
                message = str(message)
                self.fail(message, code=400, heading='Error processing form')
            except:
                s = StringIO.StringIO()
                traceback.print_exc(None, s)
                s = cgi.escape(s.getvalue())
                self.fail('error', code=400, heading='Error...',
                    content='<pre>%s</pre>'%s)
        finally:
            self.store.close()

    def fail(self, message, title="Python Packages Index", code=400,
            heading=None, headers={}, content=''):
        status = ('fail', message)
        self.page_head(title, status, heading, code, headers)
        self.wfile.write('<p class="error">%s</p>'%message)
        self.wfile.write(content)
        self.page_foot()

    def success(self, message=None, title="Python Packages Index", code=200,
            heading=None, headers={}, content=''):
        if message:
            status = ('success', message)
        else:
            status = None
        self.page_head(title, status, heading, code, headers)
        if message:
            self.wfile.write('<p class="ok">%s</p>'%message)
        self.wfile.write(content)
        self.page_foot()

    def page_head(self, title, status=None, heading=None, code=200, headers={}):
        self.handler.send_response(code)
        self.handler.send_header('Content-Type', 'text/html')
        if status is None:
            status = ('success', 'success')
        self.handler.send_header('X-Pypi-Status', status[0])
        self.handler.send_header('X-Pypi-Reason', status[1])
        for k,v in headers.items():
            self.handler.send_header(k, v)
        self.handler.end_headers()
        if heading is None: heading = title
        self.wfile.write('''
<html><head><title>%s</title>
<link rel="stylesheet" type="text/css" href="http://mechanicalcat.net/style.css">
<link rel="stylesheet" type="text/css" href="http://mechanicalcat.net/page.css">
</head>
<body>
<div id="header"><h1>%s</h1></div>
<div id="content">
'''%(title, heading))

    def page_foot(self):
        self.wfile.write('\n</div>\n</body></html>\n')

    def inner_run(self):
        ''' Figure out what the request is, and farm off to the appropriate
            handler.
        '''
        # see if the user has provided a username/password
        self.username = None
        auth = self.env.get('HTTP_CGI_AUTHORIZATION', '').strip()
        if auth:
            authtype, auth = auth.split()
            if authtype.lower() == 'basic':
                un, pw = base64.decodestring(auth).split(':')
                if self.store.has_user(un):
                    pw = sha.sha(pw).hexdigest()
                    user = self.store.get_user(un)
                    if pw != user['password']:
                        raise Unauthorised, 'Incorrect password'
                    self.username = un
                    self.store.set_user(un, self.env['REMOTE_ADDR'])

        # now handle the request
        if self.form.has_key(':action'):
            action = self.form[':action'].value
        else:
            action = 'index'

        # make sure the user has permission
        if action in ('submit', ):
            if self.username is None:
                raise Unauthorised
            if self.store.get_otk(self.username):
                raise Unauthorised

        # handle the action
        if action in 'submit verify submit_form display search_form register_form user password_reset index role role_form'.split():
            getattr(self, action)()
        else:
            raise ValueError, 'Unknown action'

        # commit any database changes
        self.store.commit()

    def index(self):
        ''' Print up an index page
        '''
        content = StringIO.StringIO()
        w = content.write
        w('<a href="?:action=search_form">search</a>\n')
        w('| <a href="?:action=role_form">admin</a>\n')
        w('| <a href="?:action=submit_form">manual submission</a>\n')
        w('| <a href="?:action=register_form">register for a login</a>\n')
        w('<ul>\n')
        spec = {}
        for k in self.form.keys():
            if k.startswith(':'):
                continue
            value = self.form[k]
            k = k.replace('-', '_')
            if type(value) == type([]):
                spec[k] = filter(None, [x.value.strip() for x in value])
            else:
                spec[k] = filter(None, [value.value.strip()])
        for name, version in self.store.query_packages(spec):
            w('<li><a href="?:action=display&name=%s&version=%s">%s %s</a>\n'%(
                urllib.quote(name), urllib.quote(version), name, version))
        w('''
</ul>
<hr>
To list your <b>distutils</b>-packaged distribution here:
<ol>
<li>Copy <a href="http://mechanicalcat.net/tech/distutils_rego/register.py">register.py</a> to your
python lib "distutils/command" directory (typically something like
"/usr/lib/python2.1/distutils/command/").
<li>Run "setup.py register" as you would normally run "setup.py" for your
distribution - this will register your distribution's metadata with the
index.
<li>... that's <em>it</em>
</ol>
<p>In a nutshell, this server is a set of three modules (
<a href="http://mechanicalcat.net/tech/distutils_rego/config.py">config.py</a>
, <a
href="http://mechanicalcat.net/tech/distutils_rego/store.py">store.py</a> and <a
href="http://mechanicalcat.net/tech/distutils_rego/webui.py">webui.py</a>)
and one config file (<a 
href="http://mechanicalcat.net/tech/distutils_rego/config.ini">config.ini</a>)
which sit over a single <a href="http://www.hwaci.com/sw/sqlite/">sqlite</a> (simple, speedy) table. The register command posts the
metadata and the web interface stores it away. The storage layer handles
searching, but the web interface doesn't expose it yet :)</p>
<p>Entries are unique by (name, version) and multiple submissions of the same
(name, version) result in updates to the existing entry.</p>
''')
        self.success(heading='Index of packages', content=content.getvalue())

    def search_form(self):
        ''' A form used to generate filtered index displays
        '''
        self.page_head('Python modules search')
        self.wfile.write('''
<form>
<input type="hidden" name=":action" value="index">
<table class="form">
<tr><th>Name:</th>
    <td><input name="name"></td>
</tr>
<tr><th>Version:</th>
    <td><input name="version"></td>
</tr>
<tr><th>Summary:</th>
    <td><input name="summary"></td>
</tr>
<tr><th>Description:</th>
    <td><input name="description"></td>
</tr>
<tr><th>Long Description:</th>
    <td><input name="long_description"></td>
</tr>
<tr><th>Keywords:</th>
    <td><input name="keywords"></td>
</tr>

<tr><th>Hidden:</th>
    <td><select name="hidden">
         <option value="0">No</option>
         <option value="1">Yes</option>
         <option value="">Don't Care</option>
        </select></td>
</tr>
<tr><td>&nbsp;</td><td><input type="submit" value="Search"></td></tr>
</table>
</form>
''')
        self.page_foot()

    def role_form(self):
        ''' A form used to maintain user Roles
        '''
        package_name = ''
        if self.form.has_key('package_name'):
            package_name = self.form['package_name'].value
            if not (self.store.has_role('Admin', package_name) or 
                    self.store.has_role('Owner', package_name)):
                raise Unauthorised
            package = '''
<tr><th>Package Name:</th>
    <td><input type="hidden" name="package_name" value="%s">%s</td>
</tr>
'''%(urllib.quote(package_name), cgi.escape(package_name))
        elif not self.store.has_role('Admin', ''):
            raise Unauthorised
        else:
            package = '''
<tr><th>Package Name:</th>
    <td><input name="package_name"></td>
</tr>
'''
        # now write the body
        s = '''
<form>
<input type="hidden" name=":action" value="role">
<table class="form">
<tr><th>User Name:</th>
    <td><input name="user_name"></td>
</tr>
%s
<tr><th>Role to Add:</th>
    <td><select name="role_name">
        <option value="Owner">Owner</option>
        <option value="Maintainer">Maintainer</option>
        </select></td>
</tr>
<tr><td>&nbsp;</td>
    <td><input type="submit" name=":operation" value="Add Role">
        <input type="submit" name=":operation" value="Remove Role"></td></tr>
</table>
</form>
'''%package
        self.success(heading='Role maintenance', content=s)

    def role(self):
        ''' Add a Role to a user.
        '''
        # required fields
        if not self.form.has_key('package_name'):
            raise FormError, 'package_name is required'
        if not self.form.has_key('user_name'):
            raise FormError, 'user_name is required'
        if not self.form.has_key('role_name'):
            raise FormError, 'role_name is required'
        if not self.form.has_key(':operation'):
            raise FormError, ':operation is required'

        # get the values
        package_name = self.form['package_name'].value
        user_name = self.form['user_name'].value
        role_name = self.form['role_name'].value

        # further validation
        if role_name not in ('Owner', 'Maintainer'):
            raise FormError, 'role_name not Owner or Maintainer'
        if not self.store.has_user(user_name):
            raise FormError, "user doesn't exist"

        # add or remove
        operation = self.form[':operation'].value
        if operation == 'Add Role':
            if self.store.has_role(role_name, package_name, user_name):
                raise FormError, 'user has that role already'
            self.store.add_role(user_name, role_name, package_name)
            message = 'Role Added OK'
        else:
            self.store.delete_role(user_name, role_name, package_name)
            message = 'Role Removed OK'

        self.success(message=message, heading='Role maintenance')

    def display(self):
        ''' Print up an entry
        '''
        content = StringIO.StringIO()
        w = content.write

        # get the appropriate package info from the database
        name = self.form['name'].value
        version = self.form['version'].value
        info = self.store.get_package(name, version)

        # top links
        un = urllib.quote(name)
        uv = urllib.quote(version)
        w('<a href="?:action=index">index</a>\n')
        w('| <a href="?:action=role_form&package_name=%s">admin</a>\n'%un)
        w('| <a href="?:action=submit_form&name=%s&version=%s"'
            '>edit</a><br>'%(un, uv))

        # now the package info
        w('<table class="form">\n')
        keys = info.keys()
        keys.sort()
        for key in keys:
            value = info[key]
            if value is None:
                value = ''
            label = key.capitalize().replace('_', ' ')
            if key in ('url', 'home_page') and value != 'UNKNOWN':
                w('<tr><th nowrap>%s: </th><td><a href="%s">%s</a></td></tr>\n'%(label,
                    value, value))
            else:
                w('<tr><th nowrap>%s: </th><td>%s</td></tr>\n'%(label, value))
        w('\n</table>\n')

        # package's journal
        w('<table class="journal">\n')
        w('<tr><th>Date</th><th>User</th><th>IP Address</th><th>Action</th></tr>\n')
        for entry in self.store.get_journal(name, version):
            w('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>\n'%(
                entry['submitted_date'], entry['submitted_by'],
                entry['submitted_from'], entry['action']))
        w('\n</table>\n')

        self.success(heading='Python module: %s %s'%(name, version),
            content=content.getvalue())

    def submit_form(self):
        ''' A form used to submit or edit package metadata.
        '''
        # are we editing a specific entry?
        info = {}
        if self.form.has_key('name') and self.form.has_key('version'):
            name = self.form['name'].value
            version = self.form['version'].value

            # permission to do this?
            if not (self.store.has_role('Owner', name) or
                    self.store.has_role('Maintainer', name)):
                raise Unauthorised, 'Not Owner or Maintainer'

            # get the stored info
            for k,v in self.store.get_package(name, version).items():
                info[k] = v

        # submission of this form requires a login, so we should check
        # before someone fills it in ;)
        if not self.username:
            raise Unauthorised, 'You must log in'

        content = StringIO.StringIO()
        w = content.write
        w('''
<form>
<input type="hidden" name=":action" value="submit">
<table class="form">
''')

        # display all the properties
        for property in 'name version author author_email maintainer maintainer_email home_page license summary description long_description keywords platform download_url hidden'.split():
            # get the existing entry
            if self.form.has_key(property):
                value = self.form[property].value
            else:
                value = info.get(property, '')
            if value is None:
                value = ''

            # form field
            if property == 'hidden':
                a = value=='1' and ' selected' or ''
                b = value=='0' and ' selected' or ''
                field = '''<select name="hidden">
                            <option value="1"%s>Yes</option>
                            <option value="0"%s>No</option>
                           </select>'''%(a,b)
            elif property.endswith('description'):
                field = '<textarea name="%s" rows="5" cols="80">%s</textarea>'%(
                    property, value)
            else:
                field = '<input size="40" name="%s" value="%s">'%(property, value)

            # now spit out the form line
            label = property.replace('_', ' ').capitalize()
            w('<tr><th>%s:</th><td>%s</td></tr>'%(label, field))

        w('''
<tr><td>&nbsp;</td><td><input type="submit" value="Add Information"></td></tr>
</table>
</form>
''')

        self.success(heading='Manual submission form',
            content=content.getvalue())

    def submit(self):
        ''' Handle the submission of distro metadata.
        '''
        # make sure the user is identified
        if not self.username:
            raise Unauthorised, \
                "You must be identified to store package information"

        # pull the package information out of the form submission
        data = {}
        for k in self.form.keys():
            if k.startswith(':'): continue
            v = self.form[k]
            if type(v) == type([]):
                data[k.lower().replace('-','_')]=','.join([x.value for x in v])
            else:
                data[k.lower().replace('-','_')] = v.value

        # validate the data
        try:
            self.validate_metadata(data)
        except ValueError, message:
            raise FormError, message

        name = data['name']
        version = data['version']

        # make sure the user has permission to do stuff
        if self.store.has_package(data['name'], data['version']) and not (
                self.store.has_role('Owner', name) or
                self.store.has_role('Maintainer', name)):
            raise Unauthorised, \
                "You are not allowed to store '%s' package information"%name

        # save off the data
        self.store.store_package(name, version, data)
        self.store.commit()

        # return a display of the package
        self.display()

    def verify(self):
        ''' Validate the input data.
        '''
        data = {}
        for k in self.form.keys():
            if k.startswith(':'): continue
            v = self.form[k]
            if type(v) == type([]):
                data[k.lower().replace('-','_')]=','.join([x.value for x in v])
            else:
                data[k.lower().replace('-','_')] = v.value
        try:
            self.validate_metadata(data)
        except ValueError, message:
            self.fail(message, heading='Package verification')
            return

        self.success(heading='Package verification', message='Validated OK')

    def validate_metadata(self, data):
        ''' Validate the contents of the metadata.

            XXX actually _do_ this
            XXX implement the "validate" :action
        '''
        if not data.has_key('name'):
            raise ValueError, 'Missing required field: name'
        if not data.has_key('version'):
            raise ValueError, 'Missing required field: version'
        if data.has_key('metadata_version'):
            del data['metadata_version']

    def register_form(self):
        ''' Throw up a form for regstering.
        '''
        self.page_head('Python package index',
            heading='Manual user registration')
        w = self.wfile.write
        w('''
<form>
<input type="hidden" name=":action" value="user">
<table class="form">
<tr><th>Username:</th>
    <td><input name="name"></td>
</tr>
<tr><th>Password:</th>
    <td><input type="password" name="password"></td>
</tr>
<tr><th>Confirm:</th>
    <td><input type="password" name="confirm"></td>
</tr>
<tr><th>Email Address:</th>
    <td><input name="email"></td>
</tr>
<tr><td>&nbsp;</td><td><input type="submit" value="Register"></td></tr>
</table>
<p>A confirmation email will be sent to the address you nominate above.</p>
<p>To complete the registration process, visit the link indicated in the
email.</p>
''')

    def user(self):
        ''' Register, update or validate a user.

            This interface handles one of three cases:
                1. new user sending in name, password and email
                2. completion of rego with One Time Key
                3. updating existing user details for currently authed user
        '''
        info = {}
        for param in 'name password email otk confirm'.split():
            if self.form.has_key(param):
                info[param] = self.form[param].value

        if info.has_key('otk'):
            if self.username is None:
                raise Unauthorised
            # finish off rego
            if info['otk'] != self.store.get_otk(self.username):
                response = 'Error: One Time Key invalid'
            else:
                # OK, delete the key
                self.store.delete_otk(info['otk'])
                response = 'Registration complete'

        elif self.username is None:
            # validate a complete set of stuff
            # new user, create entry and email otk
            name = info['name']
            if self.store.has_user(name):
                self.fail('user "%s" already exists'%name,
                    heading='User registration')
                return
            if info.has_key('confirm') and info['password'] != info['confirm']:
                self.fail("password and confirm don't match",
                    heading='User registration')
                return
            info['otk'] = self.store.store_user(name, info['password'],
                info['email'])
            info['url'] = self.config.url
            self.send_email(info['email'], rego_message%info)
            response = 'Registration OK'

        else:
            # update details
            user = self.store.get_user(self.username)
            password = info.get('password', user['password'])
            email = info.get('email', user['email'])
            self.store.store_user(self.username, password, email)
            response = 'Details updated OK'
        self.success(message=response, heading='User registration')

    def password_reset(self):
        ''' Reset the user's password and send an email to the address given.
        '''
        email = self.form['email'].value
        user = self.store.get_user_by_email(email)
        if user is None:
            self.error('email address unknown to me')
            return
        pw = ''.join([whrandom.choice(chars) for x in range(10)])
        self.store.store_user(user['name'], pw, user['email'])
        info = {'name': user['name'], 'password': pw,
            'email':user['email']}
        self.send_email(email, password_message%info)
        self.success(message='Email sent OK')

    def send_email(self, recipient, message):
        ''' Send an administrative email to the recipient
        '''
        smtp = smtplib.SMTP(self.config.mailhost)
        smtp.sendmail(self.config.adminemail, recipient, message)

