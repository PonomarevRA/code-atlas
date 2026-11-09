/** Class context only. */
class Worker {
  /** Process a typed item. */
  async run<T extends Item>(item: T, option?: string): Promise<T> {
    prepare();
    const result: T = wrap<T>(inner(item));
    if (ready(item) && check()) {
      return await persist<T>(result);
    } else {
      throw failure();
    }
  }
}
