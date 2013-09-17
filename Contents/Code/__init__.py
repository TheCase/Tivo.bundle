from time import sleep
from subprocess import *
from os import kill, chmod
from signal import *
import urllib2, cookielib, os.path
from lxml import etree
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import base64
import string
import socket
import thread

from PMS import Plugin, Log, XML, HTTP, JSON, Prefs
from PMS.MediaXML import MediaContainer, DirectoryItem, WebVideoItem, VideoItem, SearchDirectoryItem
from PMS.FileTypes import PLS
from PMS.Shorthand import _L

TIVO_CONTENT_FOLDER     = "x-tivo-container/folder"
TIVO_CONTENT_SHOW_TTS   = "video/x-tivo-raw-tts"
TIVO_CONTENT_SHOW_PES   = "video/x-tivo-raw-pes"

TIVO_PLUGIN_PREFIX   = "/video/tivo"
TIVO_BY_NAME         = "tivo-name"
TIVO_BY_IP_SHOW      = "tivo-ip-show"
TIVO_GET_SHOW        = "tivo-fetch"
TIVO_PREFS           = "prefs"

TIVO_PORT            = 49492

TIVO_XML_NAMESPACE   = 'http://www.tivo.com/developer/calypso-protocol-1.6/'
TIVO_SITE_URL        = "http://"
TIVO_LIST_PATH       = "/TiVoConnect?Command=QueryContainer&Recurse=No&Container=%2FNowPlaying"

CookiesFile = "/cookies"
CookiesJar  = cookielib.LWPCookieJar()

####################################################################################################

def Start():
  Plugin.AddRequestHandler(TIVO_PLUGIN_PREFIX, HandleRequest, "TiVo", "icon-default.jpg", "art-default.jpg")
  Plugin.AddViewGroup("InfoList", viewMode="InfoList", contentType="items")  
  Prefs.Expose("MAC", "Media Access Key")
  path = Plugin.DataPath + CookiesFile
  tvd = Plugin.ResourceFilePath("tivodecode")
  os.chmod(tvd, 0755)
  if os.path.isfile(path):
    CookiesJar.load(path)

####################################################################################################
  
def HandleRequest(pathNouns, count):
  
  # TODO: Metadata!

  if count == 0:
    return getTivoNames()

  else:
    if (pathNouns[0] == TIVO_BY_NAME):
      return getTivoShows(pathNouns[1])
    if (pathNouns[0] == TIVO_BY_IP_SHOW):
      return getTivoEpisodes(pathNouns[1], pathNouns[2], pathNouns[3])
    if (pathNouns[0] == TIVO_GET_SHOW):
      return TivoVideo(count, pathNouns)
    if (pathNouns[0] == TIVO_PREFS):
      return TivoPrefs(count, pathNouns)

####################################################################################################

def getTivoNames():
    dir = MediaContainer('art-default.jpg', title1="TiVo")

    myMAC = Prefs.Get("MAC")
    if myMAC == None:
      myMAC = ""
    if (len(myMAC) == 10):
      p1 = Popen(["mDNS", "-B", "_tivo-videos._tcp", "local"], stdout=PIPE)
      p2 = Popen(["colrm", "1", "74"], stdin=p1.stdout, stdout=PIPE)
      p3 = Popen(["grep", "-v", "Instance Name"], stdin=p2.stdout, stdout=PIPE)
      p4 = Popen(["sort"], stdin=p3.stdout, stdout=PIPE)
      p5 = Popen(["uniq"], stdin=p4.stdout, stdout=PIPE)
      sleep(2)
      kill(p1.pid, SIGTERM)
      tivolist = p5.communicate()[0]

      for line in tivolist.split('\n'):
        curtivo = line.lstrip().rstrip()
        if len(curtivo) > 0:
          Log.Add("Adding %s" % curtivo)
          dir.AppendItem(DirectoryItem(TIVO_PLUGIN_PREFIX + "/" + TIVO_BY_NAME + "/" + curtivo, curtivo, ""))    

    dir.AppendItem(SearchDirectoryItem(TIVO_PLUGIN_PREFIX + "/" + TIVO_PREFS + "/MAC", "Set your Media Access Key" , "Set your Media Access Key  [" + myMAC +"] ", ""))
    return dir.ToXML()

####################################################################################################

def TivoPrefs(count, pathNouns):
  if (count == 3):
    Prefs.Set(pathNouns[1], pathNouns[2])
    return getTivoNames()
  else:
    return getTivoNames()

####################################################################################################

def getTivoShows(tivoName):
  dir = MediaContainer('art-default.jpg', title1="TiVo", title2=tivoName)

  p1 = Popen(["mDNS", "-L", tivoName, "_tivo-videos._tcp", "local"], stdout=PIPE)
  p2 = Popen(["grep", "443"], stdin=p1.stdout, stdout=PIPE)
  p3 = Popen(["cut", "-c", "43-57"], stdin=p2.stdout, stdout=PIPE)
  sleep(2)
  kill(p1.pid, SIGTERM)
  tivolines = p3.communicate()[0]
  tivoip = tivolines.split()[0]
  url = "https://" + tivoip + ":443" + TIVO_LIST_PATH
  return getTivoShowsByIPURL(tivoip, url, dir, 1)

####################################################################################################

def getTivoEpisodes(tivoip, show_id, showname):
  dir = MediaContainer('art-default.jpg', title1="TiVo", title2=showname)
  url = "https://" + tivoip + ":443" + TIVO_LIST_PATH + "%2F" + show_id
  if showname == "HD Recordings" or showname == "TiVo Suggestions":
    return getTivoShowsByIPURL(tivoip, url, dir, 1)
  else:
    return getTivoShowsByIPURL(tivoip, url, dir, 0)

####################################################################################################

def getTivoShowsByIPURL(tivoip, url, dir, expand_name):
  dir.SetViewGroup("InfoList")
  try:
    authhandler = urllib2.HTTPDigestAuthHandler()
    authhandler.add_password("TiVo DVR", "https://" + tivoip + ":443/", "tivo", Prefs.Get("MAC"))
    opener = urllib2.build_opener(authhandler)
    pagehandle = opener.open(url)
  except IOError, e:
    Log.Add("Got a URLError trying to open %s" % url)
    if hasattr(e, 'code'):
      Log.Add("Failed with code : %s" % e.code)
      if (int(e.code) == 401):
        dir.SetMessage("Couldn't authenticate", "Failed to authenticate to tivo.  Is the Media Access Key correct?")
      else:
        dir.SetMessage("Couldn't connect", "Failed to connect to tivo")
    if hasattr(e, 'reason'):
      Log.Add("Failed with reason : %s" % e.reason)
    return dir.ToXML()
  except:
    Log.Add ("Unexpected error trying to open %s" % url)
    return

  myetree = etree.parse(pagehandle).getroot()

  for show in myetree.xpath("g:Item", namespaces={'g': TIVO_XML_NAMESPACE}):
    show_name = getNameFromXML(show, "g:Details/g:Title/text()")
    show_content_type = getNameFromXML(show, "g:Details/g:ContentType/text()")
    if (show_content_type == TIVO_CONTENT_FOLDER):
      show_total_items = int(getNameFromXML(show, "g:Details/g:TotalItems/text()"))
      show_folder_url = getNameFromXML(show, "g:Links/g:Content/g:Url/text()")
      show_folder_id = show_folder_url[show_folder_url.rfind("%2F")+3:]
      item = DirectoryItem(TIVO_PLUGIN_PREFIX + "/" + TIVO_BY_IP_SHOW + "/" + tivoip + "/" + show_folder_id + "/" +show_name, "[" + show_name + "]", "/art-default.jpg")
      dir.AppendItem(item)

    elif ((show_content_type == TIVO_CONTENT_SHOW_TTS) or
          (show_content_type == TIVO_CONTENT_SHOW_PES)) :
      show_duration = getNameFromXML(show, "g:Details/g:Duration/text()")
      show_episode_name = getNameFromXML(show,"g:Details/g:EpisodeTitle/text()")
      show_episode_num = getNameFromXML(show, "g:Details/g:EpisodeNumber/text()")
      show_desc = getNameFromXML(show, "g:Details/g:Description/text()")
      show_url = getNameFromXML(show, "g:Links/g:Content/g:Url/text()")
      show_in_progress = getNameFromXML(show,"g:Details/g:InProgress/text()")
      show_copyright = getNameFromXML(show, "g:Details/g:CopyProtected/text()")
      
      show_desc = show_desc[:show_desc.rfind("Copyright Tribune Media")]
      show_id  =  show_url[show_url.rfind("&id=")+4:]
      if (show_episode_num != ""):
        show_season_num = show_episode_num[:-2]
        show_season_ep_num = show_episode_num[-2:]

      if (show_episode_name != ""):
        extra_name = show_episode_name
      elif (show_episode_num != ""):
        extra_name = show_episode_num
      else:
        extra_name = show_id
      if (expand_name == 1):
        target_name = show_name + " : " + extra_name
      else:
        target_name = extra_name
      if show_copyright != "Yes" and show_in_progress != "Yes":
        itempath = TIVO_PLUGIN_PREFIX + "/" +TIVO_GET_SHOW +"/" + tivoip +"/" + base64.b64encode(show_url, "_;") + "/" + base64.b64encode(show_name+" : "+extra_name, "_;")
        item = VideoItem(itempath, target_name, show_desc, show_duration, "/art-default.jpg")
        if (show_episode_num != ""):
          subtitle = "Season " + show_season_num + "      Episode " + show_season_ep_num
          item.SetAttr("subtitle",subtitle)
        dir.AppendItem(item)

    else:
      Log.Add("Found a different content type: " + show_content_type)

  return dir.ToXML()

####################################################################################################

def getNameFromXML(show, name, default=""):
   result = show.xpath(name, namespaces={'g': TIVO_XML_NAMESPACE})
   if (len(result) > 0):
     return result[0]
   else:
     return default

####################################################################################################

class MyVideoHandler(BaseHTTPRequestHandler):

  def do_HEAD(self):
    try:
      self.send_response(200)
      self.send_header('Content-Type', 'video/mpeg2')
      self.end_headers()
      return
    except:
      Log.Add("Got an Error")

  def do_GET(self):
    ip = string.split(self.path[1:], "/")[0]
    url = base64.b64decode(string.split(self.path[1:], "/", 1)[1], "_;")
    try:
      self.send_response(200)
      self.send_header('Content-type', 'video/mpeg2')
      self.end_headers()

      tvd = Plugin.ResourceFilePath("tivodecode")
#      authhandler = urllib2.HTTPDigestAuthHandler()
#      authhandler.add_password("TiVo DVR", "http://" + ip + ":80/", "tivo", Prefs.Get("MAC"))
#      Log.Add("Starting httpstuff1")
#      opener = urllib2.build_opener(authhandler, urllib2.HTTPCookieProcessor(CookiesJar))
#      Log.Add("Starting httpstuff2 : " + url)
#      urlhandle = opener.open(url)
#      tivodecode = Popen([tvd, "-m", Prefs.Get("MAC"), "-"], stdin=urlhandle, stdout=PIPE)

      curlp = Popen(["/usr/bin/curl", url, "--digest", "-s", "-u", "tivo:"+Prefs.Get("MAC"), "-c", "/tmp/cookies.txt"], stdout=PIPE)
      tivodecode = Popen([tvd, "-m", Prefs.Get("MAC"), "-"],stdin=curlp.stdout, stdout=PIPE)
      while True:
          data = tivodecode.stdout.read(4192)
          if not data:
              break
          self.wfile.write(data)


      #tivodecode.communicate()

    except IOError, e:
      Log.Add("Got an IO Error")
      if hasattr(e, 'code'):
        Log.Add("Failed with code : %s" % e.code)
      if hasattr(e, 'reason'):
        Log.Add("Failed with reason :" + e.reason)
    except:
      Log.Add ("Unexpected error")

    try:
      kill(curlp.pid, SIGTERM)
      kill(tivodecode.pid, SIGTERM)
    except:
      Log.Add("Self-exit of tivodecode/curl")

    return

  def do_POST(self):
    Log.Add("Got a Post")

####################################################################################################

def TivoServerThread(ip, port):
  try:
    httpserver = HTTPServer((ip, port), MyVideoHandler)
    httpserver.serve_forever()
  except :
    Log.Add("Server Already Running")
  
####################################################################################################


def TivoVideo(count, pathNouns):
  thread.start_new_thread(TivoServerThread, ("127.0.0.1", TIVO_PORT))
  #playlist = PLS()
  #playlist.AppendTrack("http://127.0.0.1:" + str(TIVO_PORT) + "/" + pathNouns[1] + "/" + pathNouns[2], base64.b64decode(pathNouns[3], "_;"))
  #Plugin.Response["Content-Type"] = playlist.ContentType
  #return playlist.Content()
  url = "http://127.0.0.1:" + str(TIVO_PORT) + "/" + pathNouns[1] + "/" + pathNouns[2]
  return Plugin.Redirect (url)
