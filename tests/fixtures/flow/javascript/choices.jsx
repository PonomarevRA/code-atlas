export const choose = (value) => value?.send(Arg()).next(More()) ?? Fallback();

/** JSX expressions stay visible without executing component bodies. */
export function render() {
  Before();
  return <Panel item={Read()}>{Content()}</Panel>;
}
