# VTracker
> Vorth Tracker
> all the same features, just compact and easy to use, the scaled version of the original

## Verified Servers, Automatic Banning, & Member Scanning
> Verified Servers can now be applied for by server owners / admins who DM the bot with a server ID
- Verified Servers can be identified by looking at the guilds `v!banlist` embed, verified servers will show a distinct footer letting you know. This is also how you can tell if your server has been accepted for verification!
![Image](https://github.com/user-attachments/assets/9879c9f5-79be-4613-83c2-9cd1e5445280)
> Difference between non verified (top) and verified (bottom) servers

### How It Works
- You will be added to a waitlist
- An auditor will check your banlist for non-vorth alts
- If all good, you get accepted & your ban list is added to the **GLOBAL BAN LIST**
![Image](https://github.com/user-attachments/assets/0cc686ce-ea9d-473f-83e7-4ee2dea866a5)
> Your name also gets cemented forever if its in the reason (if you're banning using a bot most of the time by default it is)

## Global Ban List
> If the bot has the `ban members` permission, you can run `v!setup` or `v!massban` to automatically ban this list
- The list can be viewed publicly, and is updated soon as a new verified server is added
- The list is an array of ids, allowing for carl-bot users to `.massban` in their own server, without needing the bot

## Banning Users
- Easy to use
- `v!ban`, `v!setup`, `v!massban`, all work as the same command
![Image](https://github.com/user-attachments/assets/0182ad2c-512d-4d2e-948f-2e5e49ba5046)
- Keeps the original reason from the user who first banned them
![Image](https://github.com/user-attachments/assets/cb1ef4d9-166f-4796-897f-e59d890a9743)
- Gives live updates based on how much progress has been made
![Image](https://github.com/user-attachments/assets/54945987-9143-4ff4-948b-4297891a4834)
