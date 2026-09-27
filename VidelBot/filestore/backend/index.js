import { Router } from 'itty-router';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
};

const jsonResponse = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders },
  });

const atlasRequest = async (env, action, collection, body) => {
  const res = await fetch(
    `https://data.mongodb-api.com/app/${env.MONGODB_APP_ID}/endpoint/data/v1/action/${action}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'api-key': env.MONGODB_API_KEY,
      },
      body: JSON.stringify({
        dataSource: env.MONGODB_DATA_SOURCE,
        database: env.MONGODB_DATABASE,
        collection,
        ...body,
      }),
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Atlas API error (${res.status}): ${text}`);
  }
  return res.json();
};

const withAuth = (request, env) => {
  const authHeader = request.headers.get('Authorization');
  const secret = env.BACKEND_API_SECRET;
  if (!secret)
    return jsonResponse({ error: 'Server configuration error: missing secret' }, 500);
  if (!authHeader || authHeader !== `Bearer ${secret}`)
    return jsonResponse({ error: 'Unauthorized' }, 401);
};

const router = Router();

router.options('*', () => new Response(null, { headers: corsHeaders }));

router.post('/api/user', withAuth, async (request, env) => {
  try {
    const { userId, botUsername } = await request.json();
    if (!userId || !botUsername)
      return jsonResponse({ error: 'Missing fields' }, 400);

    const result = await atlasRequest(env, 'findOneAndUpdate', 'users', {
      filter: { userId },
      update: { $set: { userId, botUsername } },
      upsert: true,
      returnNewDocument: true,
    });

    return jsonResponse({ success: true, user: result.document });
  } catch (error) {
    return jsonResponse({ error: error.message }, 500);
  }
});

router.post('/api/link', withAuth, async (request, env) => {
  try {
    const { token, userId } = await request.json();
    if (!token || !userId)
      return jsonResponse({ error: 'Missing fields' }, 400);

    const result = await atlasRequest(env, 'findOneAndUpdate', 'links', {
      filter: { token },
      update: { $set: { token, userId } },
      upsert: true,
      returnNewDocument: true,
    });

    return jsonResponse({ success: true, link: result.document });
  } catch (error) {
    return jsonResponse({ error: error.message }, 500);
  }
});

router.get('/', async (request, env) => {
  const token = new URL(request.url).searchParams.get('url');
  if (!token)
    return new Response('Missing "url" parameter', { status: 400, headers: corsHeaders });

  try {
    const linkResult = await atlasRequest(env, 'findOne', 'links', {
      filter: { token },
    });
    if (!linkResult.document)
      return new Response('Link not found', { status: 404, headers: corsHeaders });

    const userResult = await atlasRequest(env, 'findOne', 'users', {
      filter: { userId: linkResult.document.userId },
    });
    if (!userResult.document)
      return new Response('Bot details not found', { status: 404, headers: corsHeaders });

    const redirectUrl = `https://t.me/${userResult.document.botUsername}?start=${token}`;
    return Response.redirect(redirectUrl, 302);
  } catch (error) {
    return jsonResponse({ error: error.message }, 500);
  }
});

router.all('*', () => new Response('Not Found', { status: 404, headers: corsHeaders }));

export default {
  async fetch(request, env, ctx) {
    return router.handle(request, env, ctx);
  },
};
