interface Props { title: string }
/** Render a component value with deferred click handling. */
function View(props: Props): JSX.Element {
  const click = (event: Event): void => hidden(event);
  return <Panel value={load(props.title)} onClick={() => later()}>
    {ready() ? <Title>{text()}</Title> : <Empty value={fallback()}/>}
  </Panel>;
}
