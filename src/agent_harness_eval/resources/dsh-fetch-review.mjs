// Optional evaluation hook. Page fetching still uses the native device proxy.
export async function reviewFetch(stage, url, content = '', signal) {
  const endpoint = process.env.EVAL_FETCH_REVIEW_URL;
  if (!endpoint) return;
  let response;
  try {
    const timeout = AbortSignal.timeout(60000);
    response = await fetch(endpoint, {
      method: 'POST', redirect: 'error',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({stage, url, content}),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    if (response.status === 403) throw new Error('EVAL_FETCH_DENIED');
    if (response.status !== 200 || (await response.json()).allowed !== true) throw new Error('invalid review');
  } catch (error) {
    if (error?.message === 'EVAL_FETCH_DENIED') throw new Error('EVAL_FETCH_DENIED：网页涉及当前题目的现成答案，未返回内容。请查阅通用文档或继续本地分析。');
    throw new Error('EVAL_FETCH_REVIEW_UNAVAILABLE：网页审查未完成，内容未放行；这不是违规判定。');
  }
}
