/** Read one input; callbacks remain deferred. */
export async function run(input = Seed()) {
  Before();
  const callback = () => Deferred();
  function local() { Hidden(); }
  class Local { method() { HiddenMethod(); } }
  if (Check(input)) {
    Yes();
  } else {
    No();
  }
  for (let i = Init(); CheckLoop(i); i++, Update()) {
    if (Skip(i)) continue;
    await Work(i);
    if (Done(i)) break;
  }
  for (const item of Load()) { Use(item); }
  return Wrap(await Finish(Arg()));
  Unreachable();
}
