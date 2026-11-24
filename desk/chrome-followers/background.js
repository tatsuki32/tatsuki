// Desk Followers: opens the side panel and passes the shortcuts on to it. Nothing runs by itself on Instagram.
chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(() => {});
chrome.commands.onCommand.addListener(name => chrome.runtime.sendMessage({name}).catch(() => {}));  // no panel open: nothing happens
