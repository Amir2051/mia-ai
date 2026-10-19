# Mia Social Center: platform setup

The Social Center keeps Meta/Facebook/Instagram intact and supports OAuth connections for TikTok, Pinterest, YouTube, and LinkedIn. Credentials are server-side only; never put them in frontend environment variables.

## Environment variables

Set these in `backend/.env` (or the server's secret environment), then restart/rebuild the backend:

```dotenv
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=https://mia.drivenest.info/api/social/tiktok/callback
PINTEREST_CLIENT_ID=
PINTEREST_CLIENT_SECRET=
PINTEREST_REDIRECT_URI=https://mia.drivenest.info/api/social/pinterest/callback
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=https://mia.drivenest.info/api/social/youtube/callback
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=https://mia.drivenest.info/api/social/linkedin/callback
LINKEDIN_API_VERSION=202607
```

When the app URL changes, each registered provider redirect URI and the matching environment value must change together. Callback URLs must match exactly.

## Provider setup and limitations

- **TikTok:** Create a developer app, enable Login Kit and Content Posting API, register the callback URL, request `user.info.basic`, `video.publish`, and `video.upload`, and verify the media domain. The implementation posts public HTTPS media URLs. It queries creator privacy options before posting. Unreviewed apps may be restricted to private posts; TikTok app review is required for broader direct posting.
- **Pinterest:** Create and configure an app, register the callback URL, and request `user_accounts:read`, `boards:read`, `pins:read`, and `pins:write`. Publishing creates an image Pin from a publicly accessible HTTPS image URL on a selected board. API access tier/review may be required.
- **YouTube:** Create OAuth web credentials in Google Cloud, enable YouTube Data API v3, register the callback URL, and configure the OAuth consent screen. Uploads use `youtube.upload` and `youtube.readonly`; a public HTTPS video URL is downloaded and uploaded, with a 100 MB Mia limit. Videos default to private. Google may require verification, and uploads from unverified API projects can be restricted.
- **LinkedIn:** Create a developer app, enable the appropriate sign-in and Community Management/share product access, register the callback URL, and request `openid`, `profile`, and `w_member_social`. Current implementation publishes text-only member posts; organization-page publishing and media posts are not implemented.

## Safety notes

OAuth state is signed and time-limited. Access tokens and refresh tokens are encrypted before storage. Account queries and disconnect operations are scoped to the current Shopify shop. Platform credentials and permissions are not tested against real provider accounts by local unit tests; complete a sandbox/test-account OAuth cycle after configuring each developer app.
