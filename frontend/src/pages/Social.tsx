import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Badge,
  Banner,
  BlockStack,
  Box,
  Button,
  Card,
  Collapsible,
  EmptyState,
  FormLayout,
  Modal,
  InlineStack,
  Layout,
  Link,
  Page,
  Select,
  Text,
  TextField,
} from '@shopify/polaris';
import { SendIcon } from '@shopify/polaris-icons';
import api from '../services/api';
import { CardSkeleton, EMPTY_STATE_IMAGE, ErrorBanner, NotConnected, PageSkeleton } from '../components/states';
import { errorMessage, formatDateTime } from '../lib/format';
import { showToast } from '../lib/toast';
import { useSession } from '../lib/session';

type Account = { id: number; provider: string; account_type: string; account_name: string; metadata?: Record<string, unknown> };
type Post = { id: string; message?: string; caption?: string; created_time?: string; timestamp?: string; permalink_url?: string; permalink?: string };
type PlatformConfig = { provider: string; label: string; configured: boolean; redirect_uri: string; required_env: string[] };

const TYPE_LABELS: Record<string, string> = { facebook_page: 'Facebook Page', instagram_business: 'Instagram', tiktok: 'TikTok', pinterest: 'Pinterest', youtube: 'YouTube', linkedin: 'LinkedIn' };
const EXTRA_PLATFORMS = [
  { id: 'tiktok', label: 'TikTok', description: 'Connect a TikTok account and publish supported video or photo posts.' },
  { id: 'pinterest', label: 'Pinterest', description: 'Connect Pinterest and publish product Pins to a board.' },
  { id: 'youtube', label: 'YouTube', description: 'Connect a channel and upload videos from a public HTTPS URL.' },
  { id: 'linkedin', label: 'LinkedIn', description: 'Connect your member profile and publish text posts.' },
];

export default function Social() {
  const session = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [platformConfigs, setPlatformConfigs] = useState<PlatformConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [accountId, setAccountId] = useState('');
  const [message, setMessage] = useState('');
  const [link, setLink] = useState('');
  const [instagramImageUrl, setInstagramImageUrl] = useState('');
  const [instagramCaption, setInstagramCaption] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [instagramPublishing, setInstagramPublishing] = useState(false);
  const [posts, setPosts] = useState<Record<number, Post[]>>({});
  const [instagramPosts, setInstagramPosts] = useState<Record<number, Post[]>>({});
  const [loadingPosts, setLoadingPosts] = useState<number | null>(null);
  const [disconnectTarget, setDisconnectTarget] = useState<Account | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [devOpen, setDevOpen] = useState(false);
  const [providerConnecting, setProviderConnecting] = useState<string | null>(null);
  const [tiktokAccountId, setTiktokAccountId] = useState('');
  const [tiktokMediaUrl, setTiktokMediaUrl] = useState('');
  const [tiktokCaption, setTiktokCaption] = useState('');
  const [tiktokMediaType, setTiktokMediaType] = useState('video');
  const [tiktokPrivacy, setTiktokPrivacy] = useState('SELF_ONLY');
  const [pinterestAccountId, setPinterestAccountId] = useState('');
  const [pinterestBoardId, setPinterestBoardId] = useState('');
  const [pinterestBoards, setPinterestBoards] = useState<Array<{label:string;value:string}>>([]);
  const [loadingPinterestBoards, setLoadingPinterestBoards] = useState(false);
  const [pinterestImageUrl, setPinterestImageUrl] = useState('');
  const [pinterestTitle, setPinterestTitle] = useState('');
  const [pinterestDescription, setPinterestDescription] = useState('');
  const [pinterestLink, setPinterestLink] = useState('');
  const [youtubeAccountId, setYoutubeAccountId] = useState('');
  const [youtubeVideoUrl, setYoutubeVideoUrl] = useState('');
  const [youtubeTitle, setYoutubeTitle] = useState('');
  const [youtubeDescription, setYoutubeDescription] = useState('');
  const [youtubePrivacy, setYoutubePrivacy] = useState('private');
  const [linkedinAccountId, setLinkedinAccountId] = useState('');
  const [linkedinText, setLinkedinText] = useState('');
  const [publishingProvider, setPublishingProvider] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api
      .get('/social/accounts')
      .then((res) => {
        if (res.data?.connected === false) {
          throw new Error('Mia could not verify the Shopify session for social accounts. Close Mia and reopen it from Shopify Admin → Apps, then try again.');
        }
        const rows: Account[] = res.data?.accounts ?? [];
        setAccounts(rows);
        const firstPage = rows.find((account) => account.account_type === 'facebook_page');
        if (firstPage) setAccountId((value) => value || String(firstPage.id));
        setError(null);
      })
      .catch((err) => setError(errorMessage(err, 'Could not load your social accounts')))
      .finally(() => setLoading(false));
  }, []);

  // Meta redirects back to a standalone page because the provider OAuth flow
  // leaves Shopify Admin. Relay its short-lived code/state to the still-open,
  // authenticated embedded Mia tab; the backend verifies the signed state and shop.
  useEffect(() => {
    if (window.self !== window.top) return;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const provider = params.get('oauth_provider');
    const code = params.get('oauth_code');
    const state = params.get('oauth_state');
    if (provider !== 'meta' || !code || !state) return;

    window.history.replaceState({}, '', '/social');
    const message = { type: 'mia-social-oauth', provider, code, state };
    if (window.opener && !window.opener.closed) {
      // postMessage reaches the originating embedded iframe even when browser
      // storage partitioning isolates BroadcastChannel between the two tabs.
      window.opener.postMessage(message, window.location.origin);
    } else if ('BroadcastChannel' in window) {
      const channel = new BroadcastChannel('mia-social-oauth');
      channel.postMessage({ provider, code, state });
      channel.close();
    }
    window.setTimeout(() => window.close(), 250);
  }, []);

  // The embedded tab keeps its Shopify App Bridge session while the popup
  // completes Meta login, then performs the authenticated token exchange here.
  useEffect(() => {
    if (!session.connected) return;
    const completeOAuth = (data: { provider?: string; code?: string; state?: string }) => {
      const { provider, code, state } = data;
      if (provider !== 'meta' || !code || !state) return;
      void api.post('/social/meta/complete', { code, state })
        .then((res) => {
          const count = Number(res.data?.pages_saved ?? 0);
          setNotice(count > 0
            ? 'Meta connected successfully. Mia linked ' + count + ' Facebook Page' + (count === 1 ? '' : 's') + ' and any eligible linked Instagram professional accounts.'
            : 'Meta authorization completed. No eligible Facebook Pages were returned; check that you selected the right Page permissions.');
          load();
        })
        .catch((err) => setError(errorMessage(err, 'Could not complete the social account connection')));
    };
    const onWindowMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'mia-social-oauth') return;
      completeOAuth(event.data);
    };
    window.addEventListener('message', onWindowMessage);
    let channel: BroadcastChannel | null = null;
    if ('BroadcastChannel' in window) {
      channel = new BroadcastChannel('mia-social-oauth');
      channel.onmessage = (event: MessageEvent<{ provider?: string; code?: string; state?: string }>) => completeOAuth(event.data ?? {});
    }
    return () => {
      window.removeEventListener('message', onWindowMessage);
      channel?.close();
    };
  }, [session.connected, load]);

  useEffect(() => {
    if (!session.connected) return;
    const routeState = location.state as { socialDraft?: { facebook?: string; instagram?: string; imageUrl?: string; productTitle?: string } } | null;
    if (routeState?.socialDraft) {
      const draft = routeState.socialDraft;
      if (draft.facebook) setMessage(draft.facebook);
      if (draft.instagram) setInstagramCaption(draft.instagram);
      if (draft.imageUrl) setInstagramImageUrl(draft.imageUrl);
      setNotice(draft.productTitle
        ? 'Campaign draft for ' + draft.productTitle + ' is ready. Review it before publishing.'
        : 'Campaign draft is ready. Review it before publishing.');
      navigate('/social', { replace: true, state: null });
    }
    load();
    const params = new URLSearchParams(window.location.search);
    const oauthParams = new URLSearchParams(window.location.hash.slice(1));
    const oauthProvider = oauthParams.get('oauth_provider');
    const oauthCode = oauthParams.get('oauth_code');
    const oauthState = oauthParams.get('oauth_state');
    if (oauthProvider || oauthCode || oauthState) {
      // Remove the short-lived authorization code/state from browser history
      // before making the authenticated completion request. The backend will
      // bind the callback to the currently authenticated Shopify shop.
      window.history.replaceState({}, '', '/social');
      const supported = oauthProvider === 'meta' || EXTRA_PLATFORMS.some((platform) => platform.id === oauthProvider);
      if (!supported || !oauthCode || !oauthState) {
        setError('The social connection callback was incomplete. Please start the connection again.');
        return;
      }
      void api.post('/social/' + oauthProvider + '/complete', { code: oauthCode, state: oauthState })
        .then((res) => {
          if (oauthProvider === 'meta') {
            const count = Number(res.data?.pages_saved ?? 0);
            setNotice(count > 0
              ? 'Meta connected successfully. Mia linked ' + count + ' Facebook Page' + (count === 1 ? '' : 's') + ' and any eligible linked Instagram professional accounts.'
              : 'Meta authorization completed. No eligible Facebook Pages were returned; check that you selected the right Page permissions.');
          } else {
            const label = EXTRA_PLATFORMS.find((platform) => platform.id === oauthProvider)?.label || oauthProvider;
            setNotice(label + ' connected successfully.');
          }
          load();
        })
        .catch((err) => setError(errorMessage(err, 'Could not complete the social account connection')));
      return;
    }
    const connectedProvider = params.get('connected_provider');
    if (connectedProvider) {
      const label = EXTRA_PLATFORMS.find((platform) => platform.id === connectedProvider)?.label || connectedProvider;
      setNotice(label + ' connected successfully.');
      window.history.replaceState({}, '', '/social');
    }
    if (params.get('connected') === '1') {
      const count = Number(params.get('pages') || 0);
      setNotice(count > 0
        ? 'Meta connected successfully. Mia linked ' + count + ' Facebook Page' + (count === 1 ? '' : 's') + ' and any eligible linked Instagram professional accounts.'
        : 'Meta authorization completed. No eligible Facebook Pages were returned; check that you selected the right Page permissions.');
      window.history.replaceState({}, '', '/social');
    }
  }, [session.connected, load]);

  useEffect(() => {
    if (!session.connected) return;
    api.get('/social/configuration')
      .then((res) => setPlatformConfigs((res.data?.platforms ?? []) as PlatformConfig[]))
      .catch((err) => setError(errorMessage(err, 'Could not load social platform configuration status')));
  }, [session.connected]);

  if (session.loading) return <PageSkeleton title="Social" withAside />;
  if (!session.connected) return <NotConnected title="Social" error={session.error} />;

  const pages = accounts.filter((account) => account.account_type === 'facebook_page');
  const instagramPages = pages.filter((account) => {
    const instagram = account.metadata?.instagram_business_account;
    return Boolean(instagram && (typeof instagram === 'string' || (typeof instagram === 'object' && 'id' in instagram)));
  });

  const connectMeta = async () => {
    setError(null);
    // Open synchronously from the button click so browser popup blockers do not
    // discard the user gesture while the authenticated API request is pending.
    const popup = window.open('about:blank', 'mia-meta-oauth', 'popup,width=640,height=760');
    if (!popup) {
      setError('Allow pop-ups for Mia, then click Connect Facebook again. Mia keeps this Shopify session open while Facebook authorization runs in a separate window.');
      return;
    }
    try {
      const res = await api.get('/social/meta/connect');
      const url = res.data?.authorization_url;
      if (!url) throw new Error('Meta did not return a sign-in link');
      // Keep this authenticated Shopify Admin tab open. The callback popup
      // relays its code/state back through BroadcastChannel after Meta returns.
      popup.location.href = url;
    } catch (err) {
      popup.close();
      setError(errorMessage(err, 'Could not start the Meta connection'));
    }
  };

  const connectProvider = async (provider: string) => {
    setError(null);
    setProviderConnecting(provider);
    try {
      const res = await api.get('/social/connect/' + provider);
      const url = res.data?.authorization_url;
      if (!url) throw new Error('The provider did not return a sign-in link');
      window.open(url, '_top');
    } catch (err) {
      setError(errorMessage(err, 'Could not start the social account connection'));
      setProviderConnecting(null);
    }
  };

  const publishProvider = async (provider: string, path: string, payload: Record<string, unknown>, clear: () => void) => {
    setError(null);
    setPublishingProvider(provider);
    try {
      const res = await api.post(path, payload);
      if (res.data?.audit_recorded === false) {
        showToast('Provider accepted the request, but Mia could not save activity history');
      } else {
        const status = res.data?.status === 'processing' ? 'Publishing started' : 'Published successfully';
        showToast(provider === 'youtube' ? 'YouTube video uploaded' : status + ' to ' + provider[0].toUpperCase() + provider.slice(1));
      }
      clear();
    } catch (err) {
      setError(errorMessage(err, 'Could not publish to ' + provider));
    } finally {
      setPublishingProvider(null);
    }
  };

  const loadPinterestBoards = async () => {
    const id = Number(pinterestAccountId || accounts.find((a) => a.provider === 'pinterest')?.id);
    if (!id) return;
    setLoadingPinterestBoards(true);
    setError(null);
    try {
      const res = await api.get('/social/pinterest/' + id + '/boards');
      const boards = (res.data?.boards ?? []) as Array<{ id: string; name: string }>;
      const options = boards.map((board) => ({ label: board.name, value: board.id }));
      setPinterestBoards(options);
      if (options.length && !options.some((board) => board.value === pinterestBoardId)) setPinterestBoardId(options[0].value);
      if (!options.length) setNotice('No Pinterest boards were returned for this account.');
    } catch (err) {
      setError(errorMessage(err, 'Could not load Pinterest boards'));
    } finally {
      setLoadingPinterestBoards(false);
    }
  };

  const connectTestToken = async () => {
    setError(null);
    try {
      const res = await api.post('/social/meta/test-connect');
      showToast(`Test account connected (${res.data?.pages_saved ?? 0} pages)`);
      load();
    } catch (err) {
      setError(errorMessage(err, 'Could not connect the configured test token'));
    }
  };

  const publish = async () => {
    if (!message.trim() || !accountId) return;
    setPublishing(true);
    setError(null);
    try {
      const res = await api.post('/social/meta/facebook/publish', { account_id: Number(accountId), message: message.trim(), link: link.trim() || null });
      setMessage('');
      setLink('');
      showToast(res.data?.audit_recorded === false ? 'Posted to Facebook, but activity history could not be saved' : 'Posted to Facebook');
      setPosts((value) => {
        const next = { ...value };
        delete next[Number(accountId)];
        return next;
      });
    } catch (err) {
      setError(errorMessage(err, 'Facebook did not accept the post'));
    } finally {
      setPublishing(false);
    }
  };

  const publishInstagram = async () => {
    if (!accountId || !instagramImageUrl.trim() || !instagramCaption.trim()) return;
    setInstagramPublishing(true);
    setError(null);
    try {
      const res = await api.post('/social/meta/instagram/publish', {
        account_id: Number(accountId),
        image_url: instagramImageUrl.trim(),
        caption: instagramCaption.trim(),
      });
      setInstagramCaption('');
      showToast(res.data?.audit_recorded === false ? 'Published to Instagram, but activity history could not be saved' : 'Published to Instagram');
      setInstagramPosts((value) => {
        const next = { ...value };
        delete next[Number(accountId)];
        return next;
      });
    } catch (err) {
      setError(errorMessage(err, 'Instagram did not accept the post. Confirm the Page is linked to an Instagram professional account and the image URL is publicly accessible over HTTPS.'));
    } finally {
      setInstagramPublishing(false);
    }
  };

  const loadInstagramPosts = async (id: number) => {
    setLoadingPosts(id);
    setError(null);
    try {
      const res = await api.get('/social/meta/instagram/' + id + '/monitor');
      setInstagramPosts((value) => ({ ...value, [id]: (res.data?.data ?? []) as Post[] }));
    } catch (err) {
      setError(errorMessage(err, 'Could not load recent Instagram posts'));
    } finally {
      setLoadingPosts(null);
    }
  };

  const disconnectAccount = async () => {
    if (!disconnectTarget) return;
    setDisconnecting(true);
    setError(null);
    try {
      await api.delete('/social/accounts/' + disconnectTarget.id);
      setAccounts((value) => value.filter((account) => account.id !== disconnectTarget.id));
      setPosts((value) => { const next = { ...value }; delete next[disconnectTarget.id]; return next; });
      setInstagramPosts((value) => { const next = { ...value }; delete next[disconnectTarget.id]; return next; });
      if (Number(accountId) === disconnectTarget.id) setAccountId('');
      showToast('Social page disconnected from Mia');
      setDisconnectTarget(null);
    } catch (err) {
      setError(errorMessage(err, 'Could not disconnect this social page'));
    } finally {
      setDisconnecting(false);
    }
  };

  const loadPosts = async (id: number) => {
    setLoadingPosts(id);
    setError(null);
    try {
      const res = await api.get(`/social/meta/facebook/${id}/monitor`);
      setPosts((value) => ({ ...value, [id]: (res.data?.data ?? []) as Post[] }));
    } catch (err) {
      setError(errorMessage(err, 'Could not load recent posts'));
    } finally {
      setLoadingPosts(null);
    }
  };

  return (
    <Page
      title="Social"
      subtitle="Connect your social pages, create campaigns with Mia, and publish directly where supported"
      primaryAction={{ content: 'Connect Meta', onAction: connectMeta }}
    >
      <Layout>
        <Layout.Section>
          <BlockStack gap="400">
            {notice && <Banner tone="success" onDismiss={() => setNotice(null)}><p>{notice}</p></Banner>}
            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Platform setup</Text>
                <Text as="p" tone="subdued">OAuth credentials belong in the server environment, never in the browser. Register each exact callback URL in that platform's developer console.</Text>
                {platformConfigs.map((platform) => (
                  <Box key={platform.provider} paddingBlock="200" borderBlockStartWidth="025" borderColor="border">
                    <BlockStack gap="150">
                      <InlineStack align="space-between" blockAlign="center" gap="200">
                        <Text as="h3" variant="headingSm">{platform.label}</Text>
                        <Badge tone={platform.configured ? 'success' : 'warning'}>{platform.configured ? 'Credentials configured' : 'Setup required'}</Badge>
                      </InlineStack>
                      {!platform.configured && <Text as="p" tone="subdued">Add {platform.required_env.join(' and ')} to backend/.env, then restart Mia.</Text>}
                      <Text as="p" variant="bodySm">Callback URL: {platform.redirect_uri}</Text>
                      <InlineStack>
                        <Button size="slim" variant="plain" onClick={() => {
                          void navigator.clipboard.writeText(platform.redirect_uri)
                            .then(() => showToast('Callback URL copied'))
                            .catch(() => setError('Clipboard access failed. Copy the callback URL manually.'));
                        }}>Copy callback URL</Button>
                      </InlineStack>
                    </BlockStack>
                  </Box>
                ))}
              </BlockStack>
            </Card>

            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Connect social platforms</Text>
                <Text as="p" tone="subdued">Each platform needs its own developer app credentials and approved API permissions on the server. Connection buttons show an error until credentials are configured.</Text>
                {EXTRA_PLATFORMS.map((platform) => {
                  const connected = accounts.some((account) => account.provider === platform.id);
                  return <Box key={platform.id} paddingBlock="200" borderBlockStartWidth="025" borderColor="border">
                    <InlineStack align="space-between" blockAlign="center" gap="300">
                      <BlockStack gap="100">
                        <Text as="h3" variant="headingSm">{platform.label}</Text>
                        <Text as="p" tone="subdued">{platform.description}</Text>
                      </BlockStack>
                      {connected ? <Badge tone="success">Connected</Badge> : <Button onClick={() => connectProvider(platform.id)} loading={providerConnecting === platform.id} disabled={Boolean(providerConnecting)}>Connect {platform.label}</Button>}
                    </InlineStack>
                  </Box>;
                })}
              </BlockStack>
            </Card>

            {!loading && accounts.some((account) => account.provider === 'tiktok') && (
              <Card><BlockStack gap="300"><Text as="h2" variant="headingMd">Publish to TikTok</Text>
                <Select label="TikTok account" options={accounts.filter((a) => a.provider === 'tiktok').map((a) => ({ label: a.account_name, value: String(a.id) }))} value={tiktokAccountId || String(accounts.find((a) => a.provider === 'tiktok')?.id || '')} onChange={setTiktokAccountId} />
                <Select label="Media type" options={[{label:'Video',value:'video'},{label:'Photo',value:'photo'}]} value={tiktokMediaType} onChange={setTiktokMediaType} />
                <TextField label="Public HTTPS media URL" type="url" value={tiktokMediaUrl} onChange={setTiktokMediaUrl} autoComplete="url" placeholder="https://your-store.com/media/video.mp4" />
                <TextField label="Caption" value={tiktokCaption} onChange={setTiktokCaption} multiline={3} autoComplete="off" maxLength={4000} />
                <Select label="Privacy" options={[{label:'Only me (safe default)',value:'SELF_ONLY'},{label:'Public',value:'PUBLIC_TO_EVERYONE'}]} value={tiktokPrivacy} onChange={setTiktokPrivacy} />
                <Banner tone="info"><p>TikTok requires the approved Content Posting API, verified media URL domain, and appropriate publishing scopes. Unreviewed apps may be limited to private posts.</p></Banner>
                <InlineStack align="end"><Button variant="primary" loading={publishingProvider === 'tiktok'} disabled={!tiktokMediaUrl.trim() || !tiktokCaption.trim() || Boolean(publishingProvider)} onClick={() => publishProvider('tiktok','/social/tiktok/publish',{account_id:Number(tiktokAccountId || accounts.find((a) => a.provider === 'tiktok')?.id),media_url:tiktokMediaUrl,caption:tiktokCaption,media_type:tiktokMediaType,privacy_level:tiktokPrivacy},() => {setTiktokCaption('');setTiktokMediaUrl('');})}>Publish to TikTok</Button></InlineStack>
              </BlockStack></Card>
            )}

            {!loading && accounts.some((account) => account.provider === 'pinterest') && (
              <Card><BlockStack gap="300"><Text as="h2" variant="headingMd">Publish a Pinterest Pin</Text>
                <Select label="Pinterest account" options={accounts.filter((a) => a.provider === 'pinterest').map((a) => ({ label: a.account_name, value: String(a.id) }))} value={pinterestAccountId || String(accounts.find((a) => a.provider === 'pinterest')?.id || '')} onChange={setPinterestAccountId} />
                {pinterestBoards.length > 0 ? (
                  <Select label="Pinterest board" options={pinterestBoards} value={pinterestBoardId} onChange={setPinterestBoardId} />
                ) : (
                  <TextField label="Board ID" value={pinterestBoardId} onChange={setPinterestBoardId} autoComplete="off" helpText="Load boards to choose one, or enter a board ID manually." />
                )}
                <InlineStack><Button onClick={loadPinterestBoards} loading={loadingPinterestBoards}>Load boards</Button></InlineStack>
                <TextField label="Public HTTPS image URL" type="url" value={pinterestImageUrl} onChange={setPinterestImageUrl} autoComplete="url" placeholder="https://your-store.com/images/product.jpg" />
                <TextField label="Pin title" value={pinterestTitle} onChange={setPinterestTitle} autoComplete="off" maxLength={100} />
                <TextField label="Description" value={pinterestDescription} onChange={setPinterestDescription} multiline={3} autoComplete="off" maxLength={800} />
                <TextField label="Destination link (optional)" type="url" value={pinterestLink} onChange={setPinterestLink} autoComplete="url" />
                <InlineStack align="end"><Button variant="primary" loading={publishingProvider === 'pinterest'} disabled={!pinterestBoardId.trim() || !pinterestImageUrl.trim() || Boolean(publishingProvider)} onClick={() => publishProvider('pinterest','/social/pinterest/publish',{account_id:Number(pinterestAccountId || accounts.find((a) => a.provider === 'pinterest')?.id),board_id:pinterestBoardId,image_url:pinterestImageUrl,title:pinterestTitle,description:pinterestDescription,link:pinterestLink || null},() => {setPinterestTitle('');setPinterestDescription('');setPinterestImageUrl('');setPinterestLink('');})}>Publish Pin</Button></InlineStack>
              </BlockStack></Card>
            )}

            {!loading && accounts.some((account) => account.provider === 'youtube') && (
              <Card><BlockStack gap="300"><Text as="h2" variant="headingMd">Upload to YouTube</Text>
                <Select label="YouTube channel" options={accounts.filter((a) => a.provider === 'youtube').map((a) => ({ label: a.account_name, value: String(a.id) }))} value={youtubeAccountId || String(accounts.find((a) => a.provider === 'youtube')?.id || '')} onChange={setYoutubeAccountId} />
                <TextField label="Public HTTPS video URL" type="url" value={youtubeVideoUrl} onChange={setYoutubeVideoUrl} autoComplete="url" placeholder="https://your-store.com/media/product-demo.mp4" helpText="Current local upload limit is 100 MB." />
                <TextField label="Video title" value={youtubeTitle} onChange={setYoutubeTitle} autoComplete="off" maxLength={100} />
                <TextField label="Description" value={youtubeDescription} onChange={setYoutubeDescription} multiline={3} autoComplete="off" maxLength={5000} />
                <Select label="Visibility" options={[{label:'Private (safe default)',value:'private'},{label:'Unlisted',value:'unlisted'},{label:'Public',value:'public'}]} value={youtubePrivacy} onChange={setYoutubePrivacy} />
                <InlineStack align="end"><Button variant="primary" loading={publishingProvider === 'youtube'} disabled={!youtubeVideoUrl.trim() || !youtubeTitle.trim() || Boolean(publishingProvider)} onClick={() => publishProvider('youtube','/social/youtube/publish',{account_id:Number(youtubeAccountId || accounts.find((a) => a.provider === 'youtube')?.id),video_url:youtubeVideoUrl,title:youtubeTitle,description:youtubeDescription,privacy_status:youtubePrivacy},() => {setYoutubeTitle('');setYoutubeDescription('');setYoutubeVideoUrl('');})}>Upload video</Button></InlineStack>
              </BlockStack></Card>
            )}

            {!loading && accounts.some((account) => account.provider === 'linkedin') && (
              <Card><BlockStack gap="300"><Text as="h2" variant="headingMd">Publish to LinkedIn</Text>
                <Select label="LinkedIn member" options={accounts.filter((a) => a.provider === 'linkedin').map((a) => ({ label: a.account_name, value: String(a.id) }))} value={linkedinAccountId || String(accounts.find((a) => a.provider === 'linkedin')?.id || '')} onChange={setLinkedinAccountId} />
                <TextField label="Post text" value={linkedinText} onChange={setLinkedinText} multiline={5} autoComplete="off" maxLength={3000} showCharacterCount />
                <InlineStack align="end"><Button variant="primary" loading={publishingProvider === 'linkedin'} disabled={!linkedinText.trim() || Boolean(publishingProvider)} onClick={() => publishProvider('linkedin','/social/linkedin/publish',{account_id:Number(linkedinAccountId || accounts.find((a) => a.provider === 'linkedin')?.id),text:linkedinText},() => setLinkedinText(''))}>Publish to LinkedIn</Button></InlineStack>
              </BlockStack></Card>
            )}

            {loading ? (
              <CardSkeleton />
            ) : accounts.length === 0 && !error ? (
              <Card>
                <EmptyState
                  heading="Connect Facebook and Instagram"
                  action={{ content: 'Connect with Meta', onAction: connectMeta }}
                  image={EMPTY_STATE_IMAGE}
                >
                  <p>Authorize Meta to link the Facebook Pages you manage. Mia can publish Facebook posts and, for eligible linked Instagram professional accounts, publish image posts too. You choose which Page to use.</p>
                </EmptyState>
              </Card>
            ) : accounts.length === 0 ? null : (
              <>
                {pages.length > 0 && (
                  <Card>
                    <BlockStack gap="300">
                      <Text as="h2" variant="headingMd">New Facebook post</Text>
                      <FormLayout>
                        {pages.length > 1 && (
                          <Select
                            label="Page"
                            options={pages.map((page) => ({ label: page.account_name, value: String(page.id) }))}
                            value={accountId}
                            onChange={setAccountId}
                          />
                        )}
                        <TextField
                          label="Post"
                          value={message}
                          onChange={setMessage}
                          multiline={5}
                          autoComplete="off"
                          maxLength={5000}
                          showCharacterCount
                          helpText="Tip: generate copy in SEO & marketing, then paste it here."
                        />
                        <TextField label="Link (optional)" type="url" value={link} onChange={setLink} autoComplete="off" placeholder="https://" />
                      </FormLayout>
                      <InlineStack align="end">
                        <Button variant="primary" icon={SendIcon} onClick={publish} loading={publishing} disabled={!message.trim()}>
                          Publish to Facebook
                        </Button>
                      </InlineStack>
                    </BlockStack>
                  </Card>
                )}

                {instagramPages.length > 0 && (
                  <Card>
                    <BlockStack gap="300">
                      <InlineStack align="space-between" blockAlign="center">
                        <Text as="h2" variant="headingMd">New Instagram post</Text>
                        <Badge tone="success">Professional account linked</Badge>
                      </InlineStack>
                      <Banner tone="info"><p>Instagram publishing requires a linked Instagram professional account and a publicly accessible HTTPS image URL. Mia never needs your Instagram password.</p></Banner>
                      {instagramPages.length > 1 && (
                        <Select
                          label="Facebook Page connected to Instagram"
                          options={instagramPages.map((page) => ({ label: page.account_name, value: String(page.id) }))}
                          value={instagramPages.some((page) => String(page.id) === accountId) ? accountId : String(instagramPages[0].id)}
                          onChange={setAccountId}
                        />
                      )}
                      <FormLayout>
                        <TextField
                          label="Public image URL"
                          type="url"
                          value={instagramImageUrl}
                          onChange={setInstagramImageUrl}
                          autoComplete="url"
                          placeholder="https://your-store.com/images/product.jpg"
                          helpText="The image must be publicly reachable by Meta over HTTPS. A local file or private Shopify admin URL will not work."
                        />
                        <TextField
                          label="Caption"
                          value={instagramCaption}
                          onChange={setInstagramCaption}
                          multiline={5}
                          autoComplete="off"
                          maxLength={2200}
                          showCharacterCount
                          helpText="Generate campaign copy in SEO & marketing, then paste and review it here."
                        />
                      </FormLayout>
                      <InlineStack align="end">
                        <Button variant="primary" icon={SendIcon} onClick={publishInstagram} loading={instagramPublishing} disabled={!instagramImageUrl.trim() || !instagramCaption.trim() || !instagramPages.some((page) => String(page.id) === accountId)}>
                          Publish to Instagram
                        </Button>
                      </InlineStack>
                    </BlockStack>
                  </Card>
                )}

                {pages.map((page) => (
                  <Card key={page.id}>
                    <BlockStack gap="300">
                      <InlineStack align="space-between" blockAlign="center">
                        <Text as="h2" variant="headingMd">{`Recent posts · ${page.account_name}`}</Text>
                        <Button onClick={() => loadPosts(page.id)} loading={loadingPosts === page.id}>
                          {posts[page.id] ? 'Refresh' : 'Load posts'}
                        </Button>
                      </InlineStack>
                      {instagramPages.some((linked) => linked.id === page.id) && (
                        <BlockStack gap="200">
                          <InlineStack align="space-between" blockAlign="center">
                            <Text as="h3" variant="headingSm">Instagram · {page.account_name}</Text>
                            <Button onClick={() => loadInstagramPosts(page.id)} loading={loadingPosts === page.id}>
                              {instagramPosts[page.id] ? 'Refresh Instagram' : 'Load Instagram posts'}
                            </Button>
                          </InlineStack>
                          {instagramPosts[page.id] && (instagramPosts[page.id].length === 0 ? (
                            <Text as="p" tone="subdued">No Instagram posts returned.</Text>
                          ) : instagramPosts[page.id].slice(0, 10).map((post) => (
                            <Box key={post.id} paddingBlockStart="200" borderBlockStartWidth="025" borderColor="border">
                              <BlockStack gap="100">
                                <Text as="p"><span className="mia-pre-wrap">{post.caption || '(No caption — media post)'}</span></Text>
                                <InlineStack gap="200">
                                  <Text as="span" variant="bodySm" tone="subdued">{formatDateTime(post.timestamp)}</Text>
                                  {(post.permalink || post.permalink_url) && <Link url={(post.permalink || post.permalink_url)!} external>View on Instagram</Link>}
                                </InlineStack>
                              </BlockStack>
                            </Box>
                          ))) }
                        </BlockStack>
                      )}
                      {posts[page.id] &&
                        (posts[page.id].length === 0 ? (
                          <Text as="p" tone="subdued">No posts yet.</Text>
                        ) : (
                          posts[page.id].slice(0, 10).map((post) => (
                            <Box key={post.id} paddingBlockStart="300" borderBlockStartWidth="025" borderColor="border">
                              <BlockStack gap="100">
                                <Text as="p"><span className="mia-pre-wrap">{post.message || '(No text — media post)'}</span></Text>
                                <InlineStack gap="200">
                                  <Text as="span" variant="bodySm" tone="subdued">{formatDateTime(post.created_time)}</Text>
                                  {post.permalink_url && <Link url={post.permalink_url} external>View on Facebook</Link>}
                                </InlineStack>
                              </BlockStack>
                            </Box>
                          ))
                        ))}
                    </BlockStack>
                  </Card>
                ))}
              </>
            )}
          </BlockStack>
        </Layout.Section>

        <Layout.Section variant="oneThird">
          <BlockStack gap="400">
            <Card>
              <BlockStack gap="300">
                <Text as="h2" variant="headingMd">Connected accounts</Text>
                {loading ? (
                  <Text as="p" tone="subdued">Loading…</Text>
                ) : accounts.length === 0 ? (
                  <Text as="p" tone="subdued">None yet.</Text>
                ) : (
                  accounts.map((account) => (
                    <InlineStack key={account.id} align="space-between" blockAlign="center" gap="200">
                      <Text as="span">{account.account_name}</Text>
                      <BlockStack gap="100" inlineAlign="end">
                        <Badge>{TYPE_LABELS[account.account_type] || account.account_type}</Badge>
                        <Button variant="plain" tone="critical" onClick={() => setDisconnectTarget(account)}>Disconnect</Button>
                      </BlockStack>
                    </InlineStack>
                  ))
                )}
              </BlockStack>
            </Card>
            <Modal
              open={Boolean(disconnectTarget)}
              onClose={() => setDisconnectTarget(null)}
              title="Disconnect social page?"
              primaryAction={{ content: 'Disconnect page', destructive: true, loading: disconnecting, onAction: disconnectAccount }}
              secondaryActions={[{ content: 'Cancel', onAction: () => setDisconnectTarget(null) }]}
            >
              <Modal.Section>
                <Text as="p">Mia will remove its saved connection for {disconnectTarget?.account_name || 'this page'} and clear the stored page token. This does not delete the social page or revoke access inside Meta. To revoke Mia's app permissions completely, also remove Mia from your Meta business integrations.</Text>
              </Modal.Section>
            </Modal>
            <Card>
              <BlockStack gap="200">
                <InlineStack>
                  <Button variant="plain" disclosure={devOpen ? 'up' : 'down'} onClick={() => setDevOpen((value) => !value)}>
                    Developer options
                  </Button>
                </InlineStack>
                <Collapsible open={devOpen} id="social-dev-options">
                  <BlockStack gap="200">
                    <Text as="p" variant="bodySm" tone="subdued">Connect the Meta test token configured on the server. For testing only.</Text>
                    <InlineStack>
                      <Button onClick={connectTestToken}>Connect test token</Button>
                    </InlineStack>
                  </BlockStack>
                </Collapsible>
              </BlockStack>
            </Card>
          </BlockStack>
        </Layout.Section>
      </Layout>
    </Page>
  );
}
