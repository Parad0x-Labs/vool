---
description: VOOL Browser - browse in your own browser window, open a private window, send web traffic through Tor or your VPN, and let VOOL work on web pages with your OK.
---

# VOOL Browser

VOOL Browser opens web pages in a window of its own. It uses the Google Chrome or Chromium that
is already on your computer, with a separate profile that VOOL keeps for itself. Your usual browser,
its passwords and its history are never touched.

VOOL can also work on web pages for you: open a page, read it, fill a field, press a button. It
asks you before each step.

## What you need

* Google Chrome or Chromium installed on the computer. VOOL finds it by itself. There is nothing
  else to install.
* Brave and Microsoft Edge are not supported yet. On a computer that has only one of those, VOOL
  says **VOOL Browser needs Google Chrome or Chromium on this computer. Brave and Edge are not
  supported yet.** Install Google Chrome and try again.

## Switch it on

1. Open **Settings → Privacy & Permissions**.
2. Switch on **Web lookups**. The browser needs it. If this computer is set to keep everything
   on it, the browser stays off.
3. Switch on **Browser**.
4. Press **Open the browser**, or ask VOOL in chat to open a page and press **Open browser**.

Turning **Browser** off closes every browser window, private ones too.

## Your browser window

Press **Open in a browser window**. It is on the browser bar, on the empty page and in the menu.
A real browser window opens, with VOOL's start page: a search box and the sites you visit most.

* Sign-ins, bookmarks and history are kept between starts, in VOOL's own profile.
* Search starts as DuckDuckGo. You can choose another search in the browser's settings.
* New profiles start with secure (HTTPS-only) connections and third-party cookies blocked.
* To bring bookmarks and sign-ins over from another browser, use the browser's own import page
  (Settings, then "Import bookmarks and settings"). VOOL itself never reads another browser.

### Passwords

The browser saves passwords itself, the same way Chrome does, in your computer's own password
store (the Keychain on a Mac), and fills them in again on that site. VOOL is never given a
password, and it cannot open the browser's password list.

On a Linux computer with no keyring, the browser keeps saved passwords in a store that other
programs of the same user can read.

### Downloads

Files you download are saved in a folder called **VOOL Downloads**, inside your Downloads folder.

## A private window

Choose **New private window** in the browser menu. It opens a separate window that keeps nothing:
no history, no sign-ins, no cookies. Everything it held is deleted when you close its last tab.

* A private window asks where to save each file.
* Browser plugins never run in a private window.

## Private web (Tor or your VPN)

The shield button on the browser bar is **Private web**. It sends VOOL's web traffic through Tor
or your own VPN instead of straight from your internet connection.

* One click turns it on. The first time it uses Tor, which comes with VOOL. After that it uses
  the route you chose last.
* The button shows the state: off, connecting, on, or not working.
* Turning it off asks first: **Turn Private web off? Websites will then see your own internet
  address. Browser windows that are open will be closed.**
* To use your own VPN instead, open **Settings → Privacy & Permissions → Private web** and choose
  **Tor (slower)**, **My VPN file** (a WireGuard file from your VPN account, for example Mullvad
  or Proton VPN) or **My VPN app on this computer**.

What it does:

* Websites see the address of Tor or your VPN, not your own internet address.
* It covers all of VOOL's web traffic, not only the browser: web lookups and online models too.
* If the route stops working, VOOL sends nothing rather than going around it. A browser window
  does not open while the route is down.

What it does not do:

* It does not hide who you are from a site you sign in to. For the most privacy, stay signed out
  and use local models.
* An online model still reads what you send it. It only does not see your internet address.
* With **My VPN file** or **My VPN app on this computer**, your VPN company can see your traffic
  the way your internet provider could before.
* Tor is slower, and some sites ask you to prove you are a person.

## When VOOL uses the browser

Ask in chat, for example "open the train timetable and find the last train tonight". VOOL works
in a tab of your browser window, with the sites you are signed in to.

Before each step a card appears: **VOOL wants to use the browser**. It says what VOOL asks to do
(open an address, click, type into a field, scroll) and shows the address and the text it would
enter.

* **Approve once** lets that one step run. **Refuse** stops it.
* A card that gets no answer in time runs nothing.
* **Take over** lets you use the page yourself. VOOL can't act until you press **Resume**.
* A later message can carry on, for example "now fill the address field and send the form". It
  allows only what your words name.
* If a site wants to check that a person is there, VOOL stops. Press **Show the tab**, do the
  check yourself, then tell VOOL to carry on.
* VOOL's tab never opens addresses on this computer or your home network.
* VOOL's tab closes after 30 minutes without a step.
* Payments go through your wallet, never through a web form VOOL fills.

### The separate view

In a private chat, or when **VOOL works in my browser window** is off in Settings, VOOL uses a
separate view instead: one page at a time, page scripts off, and nothing is kept when it closes.
Many modern sites do not work there. Files are small only: up to 1 MB to upload and 2 MB to
download, each with its own OK.

## Browser plugins

A browser plugin adds something to your browser window, for example an advert blocker. Browser
plugins sit in their own **Web browser** group on the Skills & Plugins page. Before you install
one, the review says in plain words what it can read and change. It runs in your browser window
only while it is enabled, and never in a private window.

**Make a browser plugin** writes a small working plugin into a folder for you to change, or ask
VOOL in chat to write it. See [Writing a plugin](plugin-authoring.md).

## Good to know

* The browser is off until you switch it on.
* VOOL does not install a browser and is not your default browser.
